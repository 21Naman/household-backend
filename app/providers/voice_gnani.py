"""Ticket #41 -- Gnani voice (live).

Two legs, two very different states of validation.

TEXT-TO-SPEECH is live and unblocked. RQ7 -- "does Gnani's noise
robustness, validated on telephony-grade 8kHz audio, generalize to
near-field kitchen ambient noise (pressure cooker, exhaust fan) via a phone
mic?" -- is a question about a microphone, and there is no microphone in
speech synthesis. The gate that used to refuse construction of this whole
class therefore now sits inside transcribe() alone, where the risk actually
lives.

SPEECH-TO-TEXT remains gated on RQ7 and additionally has no caller: the
local faster-whisper adapter was removed and two-way voice is deferred. The
API key this project holds is scoped to TTS only, so the STT leg could not
be exercised even with the gate lifted. Ticket #41's acceptance criteria for
that leg is still a MEASUREMENT: run one real kitchen audio sample through
it and record pass/fail explicitly.

HONESTY NOTE, and this matters more than it looks. The TTS request below is
now written against the real Timbre v2.5 REST contract, not a paraphrase of
it. Two earlier revisions of this file got it wrong in ways nothing caught,
because no credential existed to try it with:

  1. The first invented the path `/tts` and an `Authorization: Bearer`
     header. Neither appears anywhere in the documentation.
  2. The second fixed those but sent `speed` nested inside `audio_config`
     (it is top-level), used `audio_config.format` (the field is
     `container`), and omitted `sample_rate`, `num_channels` and
     `sample_width` from an object the OpenAPI spec marks required. It also
     dropped `voice` entirely when none was configured, though `voice` is
     required.

Each of those would have been a 400 on the first live call. The lesson is
the one docs/honest-limits.md records: an unexecuted integration is
unexecuted, however carefully it was transcribed.

The TTS request below is now VERIFIED against the production API:
scripts/probe_gnani_tts.py returned 200 with a well-formed MPEG layer III
body matching the requested audio_config exactly. tests/test_recipe_audio.py
asserts the exact JSON so it cannot drift back.

One thing the probe found that the documentation actively misled on: the
error bodies this API really sends do NOT match the published
StandardErrorResponse shape. See _error_from below -- it handles all three
observed shapes, and its tests use captured real bodies rather than the
documented one.

STILL UNVERIFIED: the STT request shape, which is inherited from the
invented code above and which the TTS-scoped key cannot exercise anyway.
Everything in transcribe() is untested for that reason.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.enums import Language
from app.providers.language import language_mismatch
from app.providers.voice import LocalVoiceProvider

# audio_config.container -> the Content-Type Gnani answers with, per the
# documented Audio Format Reference. `raw`, `mulaw` and `alaw` are omitted
# deliberately: they are telephony formats that no browser <audio> element
# will play, and this rail exists to talk to a cook holding a phone.
_MEDIA_TYPE_BY_CONTAINER = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
}


class GnaniNotYetValidated(RuntimeError):
    """Raised when the RQ7-blocked speech-to-text leg is used without
    explicitly acknowledging that RQ7 is unresolved."""


class GnaniError(RuntimeError):
    """A failed Gnani call, carrying the vendor's own error code where it
    gave one. `error_type` matters operationally: a permission/credit
    failure means check the key, RATE_LIMITED means back off, and a bad-body
    error means this codebase built the request wrong -- three very
    different things to do about it."""

    def __init__(self, message: str, *, status_code: int | None = None, error_type: str | None = None):
        self.status_code = status_code
        self.error_type = error_type
        super().__init__(message)


def _error_from(response: httpx.Response, leg: str) -> GnaniError:
    """Turn a Gnani error body into something actionable.

    Three shapes, because the live API does not emit the one its own
    documentation describes. All three were observed via
    scripts/probe_gnani_tts.py, and the first is in the docs but has never
    actually been seen:

      documented  {"success": false, "error": {"type": ..., "message": ...}}
      real 500    {"success": false, "message": ..., "status_code": 500}
      real 429    {"detail": {"error_code": ..., "message": ..., "status_code": 429}}

    Parsing only the documented shape is what the first version did, and it
    produced `type=None message=None` for every real failure -- an error
    reporter that reports nothing. Try each, in that order.

    A body that is absent or unparseable (a gateway returning HTML, say)
    degrades to the status code rather than masking the real failure with a
    JSONDecodeError.
    """
    error_type: str | None = None
    message: str | None = None
    try:
        payload = response.json()
        documented = payload.get("error") or {}
        nested = payload.get("detail") or {}
        if not isinstance(documented, dict):
            documented = {}
        if not isinstance(nested, dict):
            nested = {}
        error_type = documented.get("type") or nested.get("error_code")
        message = documented.get("message") or nested.get("message") or payload.get("message")
    except Exception:  # noqa: BLE001 -- any malformed body lands here on purpose
        pass

    described = f"{error_type}: {message}" if error_type and message else (message or response.reason_phrase)
    hint = ""
    if response.status_code == 403:
        hint = " (check the API key, its scope, the organization status, and remaining credits)"
    elif response.status_code == 429:
        hint = " (rate limited — back off rather than retrying immediately)"
    return GnaniError(
        f"Gnani {leg} failed with HTTP {response.status_code}: {described}{hint}",
        status_code=response.status_code,
        error_type=error_type,
    )


@dataclass
class GnaniTranscript:
    transcript: str
    language: str
    confidence: float


class GnaniVoiceProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        tts_model: str = "timbre-v2.5",
        audio_config: dict[str, object] | None = None,
        speed: float = 1.0,
        timeout_seconds: float = 15.0,
        acknowledge_unvalidated_assumptions: bool = False,
    ) -> None:
        audio_config = dict(audio_config or {"container": "mp3", "bitrate": "128k"})
        container = audio_config.get("container")
        if container not in _MEDIA_TYPE_BY_CONTAINER:
            raise ValueError(
                f"Unsupported Gnani audio container {container!r}; "
                f"expected one of {', '.join(sorted(_MEDIA_TYPE_BY_CONTAINER))}"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.tts_model = tts_model
        self.audio_config = audio_config
        self.speed = speed
        self.timeout_seconds = timeout_seconds
        # Retained for the STT leg only; see transcribe().
        self.acknowledge_unvalidated_assumptions = acknowledge_unvalidated_assumptions

    # -- text to speech (live) -------------------------------------------

    def synthesize(self, text: str, language: Language, voice: str | None = None) -> bytes:
        """Generic: text in, audio bytes out. Nothing recipe-shaped here --
        composing speakable sentences is app/core/recipe_briefing.py's job,
        and happens before this is called.

        `voice` is required by the API, so it is required here. Callers
        resolve it through app/providers/language.py, which guarantees one.
        """
        if not text.strip():
            raise ValueError("Nothing to synthesize: the briefing text is empty")
        if not voice:
            raise ValueError(
                "Gnani requires a voice name; resolve one via "
                "app.providers.language.resolve_voiced_language before calling this"
            )
        payload = {
            "text": text,
            "voice": voice,
            "model": self.tts_model,
            "language": language.value,
            # Top-level, NOT inside audio_config. Getting this wrong is a 400.
            "speed": self.speed,
            "audio_config": self.audio_config,
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/tts/inference",
                    json=payload,
                    headers={"X-API-Key-ID": self.api_key},
                )
        except httpx.HTTPError as exc:
            raise GnaniError(f"Gnani TTS failed: {exc}") from exc

        if response.status_code != 200:
            raise _error_from(response, "TTS")
        if not response.content:
            raise GnaniError("Gnani TTS returned an empty audio body")
        return response.content

    def audio_media_type(self) -> str:
        return _MEDIA_TYPE_BY_CONTAINER[self.audio_config["container"]]

    # -- speech to text (still gated on RQ7) ------------------------------

    def transcribe(self, audio: bytes, suffix: str = ".webm", expected_language: str | None = None) -> dict:
        if not self.acknowledge_unvalidated_assumptions:
            raise GnaniNotYetValidated(
                "Gnani speech-to-text is BLOCKED on RQ7 (kitchen-noise generalization from "
                "telephony-grade audio, unverified). Run the one-sample kitchen-audio test "
                "described in the build map's Ticket #41 first; pass "
                "acknowledge_unvalidated_assumptions=True only once you have a recorded "
                "pass/fail, and ship text-primary if it fails. Text-to-speech is unaffected "
                "by RQ7 and needs no acknowledgement. Note also that the key this project "
                "holds is scoped to TTS only, so this call would be refused regardless."
            )
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/stt",
                    content=audio,
                    headers={"X-API-Key-ID": self.api_key, "Content-Type": "audio/webm"},
                )
        except httpx.HTTPError as exc:
            raise GnaniError(f"Gnani STT failed: {exc}") from exc

        if response.status_code != 200:
            raise _error_from(response, "STT")
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise GnaniError("Gnani STT returned a body that is not JSON") from exc

        result = {"transcript": payload["transcript"], "language": payload.get("language", "unknown")}
        result["language_mismatch_flagged"] = language_mismatch(result["language"], expected_language) if expected_language else False
        return result


class GnaniBackedVoiceProvider(LocalVoiceProvider):
    """The live rail as the container wires it.

    GnaniVoiceProvider above is a pure vendor client -- speech in, speech
    out -- and deliberately has no reply(). Brief generation is a *model*
    call, so choosing the live voice rail must not quietly change which
    model writes the cook's words. This composes the two: replies keep
    coming from the injected ModelProvider exactly as they do offline, and
    only synthesis reaches Gnani. It mirrors MockVoiceProvider, which is
    the same shape with a tone generator in place of the vendor.
    """

    def __init__(self, model_provider, gnani: GnaniVoiceProvider) -> None:
        super().__init__(model_provider)
        self.gnani = gnani

    def synthesize(self, text: str, language: Language, voice: str | None = None) -> bytes:
        return self.gnani.synthesize(text, language, voice)

    def audio_media_type(self) -> str:
        return self.gnani.audio_media_type()

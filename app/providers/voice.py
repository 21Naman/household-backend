"""Ticket #25 — the outbound half of the cook interface.

The cook's speech was transcribed but nothing replied
-- the interface was one-directional. Bible §3.5 calls bidirectional cook
dialogue the least-copied capability in the entire competitor scan, and
§3.6 calls it the system's strongest, least-substitutable AI use. A
listening-only system cannot demonstrate the thing the whole
differentiation rests on.

This is one of the three sanctioned LLM uses (Bible §3.6 / §4.1 "AI" row):
generation is allowed here, unlike anywhere in app/services.py. Register
must follow CookProfile.skill_level -- over-explaining to an expert cook
reads as worse, not better.

Bidirectional voice goes: STT -> this module's reply() (brief
generation) -> synthesis. The local faster-whisper adapter that used to
serve the STT leg has been removed now that Gnani credentials exist; Gnani
is the only STT/TTS provider in the codebase, and its STT leg stays gated
on RQ7. `transcribe` therefore has no working implementation here and says
so loudly rather than pretending.

This class is the offline base. It does real, model-backed reply
generation and cannot synthesize audio; MockVoiceProvider subclasses it to
add a fake synthesis leg for offline demos, and GnaniVoiceProvider is the
live rail. app/core/container.py chooses between them.
"""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass

from app.enums import Language

_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {"brief": {"type": "string"}},
    "required": ["brief"],
    "additionalProperties": False,
}

_REGISTER_BY_SKILL = {
    "beginner": "Explain each step in detail, including timing and visual cues for doneness. Be encouraging.",
    "intermediate": "Give clear steps without over-explaining basics. Note any unusual substitution.",
    "expert": "Be brief. State the dish, key substitution if any, and anything unusual about this batch only.",
}


class VoiceSynthesisUnavailable(RuntimeError):
    """No text-to-speech rail is configured in this build.

    Distinct from a synthesis *failure*: this is the normal, expected state
    of the zero-credential offline build, and the route reports it as such
    rather than as a 503. Text is the guaranteed channel; audio is an
    enhancement on top of it.
    """


@dataclass
class CookBrief:
    text: str
    language: str
    audio: bytes | None = None
    language_mismatch_flagged: bool = False


class LocalVoiceProvider:
    """BUILD IT implementation of app.core.interfaces.VoiceProvider.
    Reply generation uses the
    injected ModelProvider (Ollama locally, Bedrock in SHIP IT) — this
    class itself never talks to a model API directly, so it works
    unchanged regardless of which ModelProvider it's given."""

    def __init__(self, model_provider):
        self.model_provider = model_provider

    def transcribe(self, audio: bytes, suffix: str = ".webm", expected_language: str | None = None) -> dict:
        """Declared, not served. Two-way voice is deferred: the local
        Whisper adapter is gone and Gnani's STT leg is gated on RQ7, so
        there is no transcription path today. This raises rather than
        returning an empty transcript, because a silently empty transcript
        is the kind of thing that gets demoed as working."""
        raise NotImplementedError(
            "Speech-to-text is not available: the local Whisper adapter was removed and "
            "Gnani STT remains gated on RQ7. Two-way voice is deferred, not abandoned."
        )

    def reply(self, dish_name: str, instructions: str, language: str, skill_level: str) -> CookBrief:
        """Generate a register-appropriate brief in the cook's language.
        Falls back to a templated brief (no model call) if the model is
        unavailable -- text stays the guaranteed path per Ticket #25's
        watch-out, matching the fallback philosophy elsewhere (vision.py
        falls back to typed/manual entry the same way)."""
        register = _REGISTER_BY_SKILL.get(skill_level.strip().lower(), _REGISTER_BY_SKILL["intermediate"])
        prompt = (
            f"You are briefing a home cook in {language}. Write ONLY in {language} "
            f"(use the Latin script if that is how {language} is normally typed casually). "
            f"Register: {register}\n\n"
            f"Dish: {dish_name}\nInstructions/substitutions to convey: {instructions}\n\n"
            'Return JSON: {"brief": "<the message to the cook, in ' + language + '>"}'
        )
        try:
            result = self.model_provider.generate_structured(prompt, schema=_BRIEF_SCHEMA)
            text = result["brief"]
        except Exception:
            text = f"[{language}, {skill_level}] {dish_name}: {instructions}"

        # audio stays None: no audio synthesis library is wired in for BUILD
        # IT, so text is the guaranteed channel (Ticket #25's watch-out).
        # Ticket #41 supplies live Gnani TTS behind this same contract.
        return CookBrief(text=text, language=language, audio=None)

    def synthesize(self, text: str, language: Language, voice: str | None = None) -> bytes:
        """No local TTS engine is wired in. Text remains the guaranteed
        channel -- the same fallback philosophy reply() already follows."""
        raise VoiceSynthesisUnavailable(
            "This build has no text-to-speech rail configured. Set HOUSEHOLD_GNANI_API_KEY "
            "and HOUSEHOLD_GNANI_MOCK_ENABLED=false for live audio, or leave the mock enabled."
        )

    def audio_media_type(self) -> str:
        raise VoiceSynthesisUnavailable("This build has no text-to-speech rail configured.")


def join_audio(parts: list[bytes], media_type: str) -> bytes:
    """Stitch per-chunk synthesis results into one playable file.

    Chunking only happens when a briefing exceeds the configured character
    limit, so this is the rare path -- but it has to be format-aware,
    because concatenating WAV files byte-for-byte produces a file whose
    header claims the length of the first chunk alone and which every
    player truncates. MP3 is a frame stream and does concatenate.

    Anything else raises. Ogg in particular is NOT a concatenable stream:
    each file carries its own page headers and serial number, so joining two
    of them yields something players either truncate or reject. Failing here
    is the point -- a rare path that silently produces a half-length briefing
    is worse than one that stops and says why.
    """
    if not parts:
        raise ValueError("No audio to join")
    if len(parts) == 1:
        return parts[0]
    if media_type == "audio/mpeg":
        return b"".join(parts)
    if media_type != "audio/wav":
        raise ValueError(
            f"Cannot join multiple {media_type} chunks into one file. Either configure a "
            "joinable container (mp3 or wav) or raise HOUSEHOLD_GNANI_TTS_MAX_CHARS so the "
            "briefing fits in a single request."
        )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        params = None
        for part in parts:
            with wave.open(io.BytesIO(part), "rb") as source:
                if params is None:
                    params = source.getparams()
                    output.setnchannels(params.nchannels)
                    output.setsampwidth(params.sampwidth)
                    output.setframerate(params.framerate)
                output.writeframes(source.readframes(source.getnframes()))
    return buffer.getvalue()

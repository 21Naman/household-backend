"""Ticket #41 -- Gnani voice (live). BLOCKED on RQ7.

RQ7: does Gnani's noise robustness, validated on telephony-grade 8kHz
audio, generalize to near-field kitchen ambient noise (pressure cooker,
exhaust fan) via a phone mic? Not established either way in public
material -- genuinely open, not a confirmed limitation, per Bible §3.1.

The Bible's Assumption Register rates this low-to-medium confidence with
the stated consequence that voice may need text as the PRIMARY channel,
not a backup. This module is implemented against the Gnani STT/TTS API as
documented, but -- like Ticket #40's Pine Labs provider -- refuses to run
live unless the caller explicitly acknowledges RQ7 is unresolved, so
nobody presents an untested kitchen-noise claim as validated.

Ticket #41's actual acceptance criteria is a MEASUREMENT: run one real
kitchen audio sample through this and record pass/fail explicitly. If it
fails, LocalVoiceProvider's text-only reply path (app/providers/voice.py)
ships as primary and the submission says so -- Bible §4.5 is explicit that
honesty here scores better than confidence.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.providers.whisper import language_mismatch


class GnaniNotYetValidated(RuntimeError):
    """Raised whenever this provider is used without explicitly
    acknowledging that RQ7 is unresolved."""


class GnaniError(RuntimeError):
    pass


@dataclass
class GnaniTranscript:
    transcript: str
    language: str
    confidence: float


class GnaniVoiceProvider:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: float = 15.0, acknowledge_unvalidated_assumptions: bool = False):
        if not acknowledge_unvalidated_assumptions:
            raise GnaniNotYetValidated(
                "Gnani voice is BLOCKED on RQ7 (kitchen-noise generalization from telephony-grade "
                "audio, unverified). Run the one-sample kitchen-audio test described in the build "
                "map's Ticket #41 first; pass acknowledge_unvalidated_assumptions=True only once you "
                "have a recorded pass/fail, and ship text-primary if it fails."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def transcribe(self, audio: bytes, suffix: str = ".webm", expected_language: str | None = None) -> dict:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/stt",
                    content=audio,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "audio/webm"},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise GnaniError(f"Gnani STT failed: {exc}") from exc

        result = {"transcript": payload["transcript"], "language": payload.get("language", "unknown")}
        result["language_mismatch_flagged"] = language_mismatch(result["language"], expected_language) if expected_language else False
        return result

    def synthesize(self, text: str, language: str) -> bytes:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/tts",
                    json={"text": text, "language": language},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            raise GnaniError(f"Gnani TTS failed: {exc}") from exc

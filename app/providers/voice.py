"""Ticket #25 — the outbound half of the cook interface.

app/providers/whisper.py transcribes the cook's speech but nothing replied
-- the interface was one-directional. Bible §3.5 calls bidirectional cook
dialogue the least-copied capability in the entire competitor scan, and
§3.6 calls it the system's strongest, least-substitutable AI use. A
listening-only system cannot demonstrate the thing the whole
differentiation rests on.

This is one of the three sanctioned LLM uses (Bible §3.6 / §4.1 "AI" row):
generation is allowed here, unlike anywhere in app/services.py. Register
must follow CookProfile.skill_level -- over-explaining to an expert cook
reads as worse, not better.

Bidirectional voice goes: Whisper (local STT, unchanged) -> this module's
reply() (brief generation; audio is left unset locally) -> Ticket #41's
live Gnani STT/TTS eventually replaces both ends behind the same
VoiceProvider interface.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.providers.whisper import language_mismatch

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


@dataclass
class CookBrief:
    text: str
    language: str
    audio: bytes | None = None
    language_mismatch_flagged: bool = False


class LocalVoiceProvider:
    """BUILD IT implementation of app.core.interfaces.VoiceProvider.
    Transcription delegates to WhisperProvider; reply generation uses the
    injected ModelProvider (Ollama locally, Bedrock in SHIP IT) — this
    class itself never talks to a model API directly, so it works
    unchanged regardless of which ModelProvider it's given."""

    def __init__(self, model_provider):
        self.model_provider = model_provider
        self._whisper = None  # lazily constructed; faster-whisper import is heavy

    def transcribe(self, audio: bytes, suffix: str = ".webm", expected_language: str | None = None) -> dict:
        from app.providers.whisper import WhisperProvider
        from app.settings import get_settings

        if self._whisper is None:
            settings = get_settings()
            self._whisper = WhisperProvider(model_name=settings.whisper_model)
        result = self._whisper.transcribe(audio, suffix=suffix)

        mismatch = False
        if expected_language:
            mismatch = language_mismatch(result.get("language", "unknown"), expected_language)
        result["language_mismatch_flagged"] = mismatch
        return result

    def reply(self, dish_name: str, instructions: str, language: str, skill_level: str) -> CookBrief:
        """Generate a register-appropriate brief in the cook's language.
        Falls back to a templated brief (no model call) if the model is
        unavailable -- text stays the guaranteed path per Ticket #25's
        watch-out, matching the fallback philosophy elsewhere (vision.py,
        whisper.py both fall back to typed/manual entry)."""
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

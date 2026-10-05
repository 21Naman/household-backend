"""Language resolution shared by every voice provider.

`language_mismatch` used to live in app/providers/whisper.py. It never had
anything to do with Whisper beyond the shape of the code it compares
against -- it is a plain dict lookup with no faster-whisper import -- and
app/providers/voice_gnani.py imported it from there, which meant deleting
the Whisper adapter would have broken the Gnani provider. It lives here now
so that no voice provider depends on another voice provider.

This module also resolves the free-text language name stored on
CookProfile ("Hindi", "Hinglish") to the typed app.enums.Language that the
TTS call and the rewrite prompt both read. An unrecognised name resolves to
None, never to a guess: a briefing delivered confidently in the wrong
language is worse for a cook than no audio at all, so the caller degrades
to text.
"""
from __future__ import annotations

from app.enums import Language

# Ticket #25's cross-check: a two-letter STT language code (e.g. "en")
# against a household/cook-profile language name (e.g. "Tamil"). Not exact
# science -- this is a data-quality signal, not a hard gate, per Ticket #25's
# spec ("flag mismatches ... rather than silently trusting it").
_LANGUAGE_NAME_TO_CODE = {
    "english": "en", "hindi": "hi", "tamil": "ta", "telugu": "te",
    "bengali": "bn", "marathi": "mr", "gujarati": "gu", "kannada": "kn",
    "malayalam": "ml", "punjabi": "pa", "urdu": "ur",
}


def language_mismatch(detected_code: str, expected_language_name: str) -> bool:
    """Returns True when an STT engine's auto-detected language code
    disagrees with the household/cook's stored language. Unknown expected
    languages never raise a mismatch (nothing to compare against)."""
    expected_code = _LANGUAGE_NAME_TO_CODE.get(expected_language_name.strip().lower())
    if expected_code is None or not detected_code or detected_code == "unknown":
        return False
    return detected_code.lower() != expected_code


def resolve_language(profile_language: str | None, language_map: dict[str, str]) -> Language | None:
    """Map a stored profile language name onto a typed Language.

    `language_map` is Settings.gnani_language_map -- operator-editable, so
    a household can be retargeted at a different voice model without a code
    change. Returns None when the name is not in the map, and callers must
    treat that as "no audio", not as "use the default".
    """
    if not profile_language:
        return None
    code = language_map.get(profile_language.strip().casefold())
    if code is None:
        return None
    try:
        return Language(code)
    except ValueError:
        # A hand-edited setting naming a code Gnani does not serve. Fail to
        # text rather than sending it and hoping.
        return None


def voice_for(language: Language, voice_map: dict[str, str]) -> str | None:
    """The configured Gnani voice for a language, or None if unconfigured."""
    return voice_map.get(language.value)


def resolve_voiced_language(
    profile_language: str | None,
    language_map: dict[str, str],
    voice_map: dict[str, str],
    default_code: str,
) -> tuple[Language, str]:
    """Resolve to a language that can actually be spoken, always.

    `resolve_language` above is the honest primitive: it answers "what did the
    cook say their language was" and returns None when it does not know. That
    is the right answer to that question, but it is not enough on its own,
    because `voice` is a required field in Gnani's request and only a few
    languages have a configured voice. A cook profile saying "Tamil" used to
    resolve cleanly to ta-IN and then get no audio at all.

    So this composes on top: keep the cook's own language when it resolves
    *and* has a voice, and otherwise fall back to the configured default
    (Hinglish). That is not the model guessing a language -- it is an
    explicit, named, operator-editable default, and a code-mixed briefing is
    something an Indian home cook can follow regardless.

    Raises if the configured default is itself unusable, because a
    misconfigured fallback must fail at the first request rather than quietly
    sending a voiceless payload.
    """
    language = resolve_language(profile_language, language_map)
    if language is not None:
        voice = voice_for(language, voice_map)
        if voice is not None:
            return language, voice

    try:
        fallback = Language(default_code)
    except ValueError as exc:
        raise ValueError(
            f"gnani_default_language={default_code!r} is not a language Gnani serves"
        ) from exc
    fallback_voice = voice_for(fallback, voice_map)
    if fallback_voice is None:
        raise ValueError(
            f"gnani_default_language={default_code!r} has no voice in gnani_voice_map; "
            "audio cannot be synthesized for any household until one is configured"
        )
    return fallback, fallback_voice

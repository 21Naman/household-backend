"""Offline text-to-speech stand-in — the BUILD IT default.

The live Gnani rail cannot be exercised without credentials and a network,
but everything upstream of the vendor HTTP call can: the rewrite step, the
registry gate, the daily cap, the cache, the chunker, the route and the UI.
This provider exists so all of that is demoable and testable offline, and
so the honest claim stays precise -- the pipeline is tested, the Gnani
request shape is not.

It subclasses LocalVoiceProvider rather than reimplementing it: reply()
here is still a real model call, because the only thing being faked is the
vendor. What comes back is a genuinely valid WAV file so a browser
<audio> element really plays it -- a placeholder tone, not speech, and
audibly so. Nobody should be able to mistake this for the live rail.
"""
from __future__ import annotations

import io
import math
import struct
import wave

from app.enums import Language
from app.providers.voice import LocalVoiceProvider

_SAMPLE_RATE = 16000
# Roughly the pace of spoken Indian-English/Hinglish instructions. Only used
# to make the placeholder's duration track the real briefing's length, so a
# too-long rewrite is obvious when you play it.
_CHARS_PER_SECOND = 14.0
_MAX_SECONDS = 60.0


class MockVoiceProvider(LocalVoiceProvider):
    """A VoiceProvider whose synthesis leg is a tone generator."""

    def synthesize(self, text: str, language: Language, voice: str | None = None) -> bytes:
        if not text.strip():
            raise ValueError("Nothing to synthesize: the briefing text is empty")
        seconds = min(max(len(text) / _CHARS_PER_SECOND, 0.5), _MAX_SECONDS)
        return _tone_wav(seconds)

    def audio_media_type(self) -> str:
        return "audio/wav"


def _tone_wav(seconds: float) -> bytes:
    """A soft, slowly pulsing 220 Hz tone. Pulsed rather than continuous so
    it reads as a placeholder for speech instead of a test signal, and quiet
    enough not to startle anyone demoing at full volume."""
    total = int(_SAMPLE_RATE * seconds)
    frames = bytearray()
    for index in range(total):
        t = index / _SAMPLE_RATE
        envelope = 0.35 * (0.55 + 0.45 * math.sin(2 * math.pi * 2.5 * t))
        sample = int(envelope * 12000 * math.sin(2 * math.pi * 220.0 * t))
        frames += struct.pack("<h", sample)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(_SAMPLE_RATE)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()

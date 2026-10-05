"""Tests that don't require live AWS/rail credentials: the BLOCKED guards
on Tickets #40/#41 refuse by default, exactly as designed.
"""
from __future__ import annotations

import pytest

from app.providers.payments_pinelabs import PineLabsNotYetValidated, PineLabsPaymentProvider
from app.enums import Language
from app.providers.voice_gnani import GnaniError, GnaniNotYetValidated, GnaniVoiceProvider


def test_pinelabs_refuses_without_explicit_acknowledgment():
    with pytest.raises(PineLabsNotYetValidated):
        PineLabsPaymentProvider(client_id="x", client_secret="y", base_url="https://example.com")


def test_pinelabs_constructs_when_acknowledged():
    provider = PineLabsPaymentProvider(
        client_id="x", client_secret="y", base_url="https://example.com", acknowledge_unvalidated_assumptions=True
    )
    assert provider.client_id == "x"


# RQ7 asks whether Gnani's noise robustness generalizes to a kitchen via a
# phone mic. That is a question about a microphone, so the gate guards the
# speech-to-text leg and nothing else. These tests pin that boundary: moving
# the guard back to __init__ would block text-to-speech for a reason that
# has never applied to it, and removing it from transcribe() would let an
# unvalidated kitchen-noise claim ship as though it were measured.


def test_gnani_constructs_without_acknowledgment_because_tts_is_unaffected():
    provider = GnaniVoiceProvider(api_key="x", base_url="https://example.com")
    assert provider.api_key == "x"


def test_gnani_speech_to_text_still_refuses_without_acknowledgment():
    provider = GnaniVoiceProvider(api_key="x", base_url="https://example.com")
    with pytest.raises(GnaniNotYetValidated):
        provider.transcribe(b"audio-bytes")


def test_gnani_speech_to_text_permitted_once_acknowledged():
    provider = GnaniVoiceProvider(
        api_key="x", base_url="https://example.com", acknowledge_unvalidated_assumptions=True
    )
    # Gets past the RQ7 gate and fails on the network instead, which is the
    # point: the guard is no longer what stops the call.
    with pytest.raises(GnaniError):
        provider.transcribe(b"audio-bytes")


def test_gnani_synthesis_never_asks_for_rq7_acknowledgment():
    provider = GnaniVoiceProvider(api_key="x", base_url="https://example.com")
    with pytest.raises(GnaniError):
        provider.synthesize("do sau gram chawal", Language.HI_EN, voice="Poorvi")

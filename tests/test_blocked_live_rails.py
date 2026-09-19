"""Tests that don't require live AWS/rail credentials: the BLOCKED guards
on Tickets #40/#41 refuse by default, exactly as designed.
"""
from __future__ import annotations

import pytest

from app.providers.payments_pinelabs import PineLabsNotYetValidated, PineLabsPaymentProvider
from app.providers.voice_gnani import GnaniNotYetValidated, GnaniVoiceProvider


def test_pinelabs_refuses_without_explicit_acknowledgment():
    with pytest.raises(PineLabsNotYetValidated):
        PineLabsPaymentProvider(client_id="x", client_secret="y", base_url="https://example.com")


def test_pinelabs_constructs_when_acknowledged():
    provider = PineLabsPaymentProvider(
        client_id="x", client_secret="y", base_url="https://example.com", acknowledge_unvalidated_assumptions=True
    )
    assert provider.client_id == "x"


def test_gnani_refuses_without_explicit_acknowledgment():
    with pytest.raises(GnaniNotYetValidated):
        GnaniVoiceProvider(api_key="x", base_url="https://example.com")


def test_gnani_constructs_when_acknowledged():
    provider = GnaniVoiceProvider(api_key="x", base_url="https://example.com", acknowledge_unvalidated_assumptions=True)
    assert provider.api_key == "x"

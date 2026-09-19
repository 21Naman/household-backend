from __future__ import annotations

import pytest

from app.core.registry import ToolCallRefused, ToolKind, ToolRegistry
from app.enums import SpendTier


class _FakePaymentProvider:
    def authorize(self, household_id, amount_inr, tier):
        return {"executed": True}


class _FakeVoiceProvider:
    def reply(self, **kwargs):
        return {"text": "ok"}


def _registry():
    r = ToolRegistry()
    r.register(ToolKind.PAYMENTS, _FakePaymentProvider())
    r.register(ToolKind.VOICE, _FakeVoiceProvider())
    return r


def test_payments_refused_without_green_tier():
    r = _registry()
    with pytest.raises(ToolCallRefused):
        r.invoke(
            ToolKind.PAYMENTS,
            "authorize",
            {"tier": SpendTier.YELLOW, "budget_check_passed": True},
            household_id=1,
            amount_inr=100,
            tier=SpendTier.YELLOW,
        )
    assert len(r.blocked_calls.entries) == 1
    assert r.blocked_calls.entries[0]["tool"] == "payments"


def test_payments_refused_without_budget_check():
    r = _registry()
    with pytest.raises(ToolCallRefused):
        r.invoke(
            ToolKind.PAYMENTS,
            "authorize",
            {"tier": SpendTier.GREEN, "budget_check_passed": False},
            household_id=1,
            amount_inr=100,
            tier=SpendTier.GREEN,
        )


def test_payments_allowed_when_green_and_budget_check_passes():
    r = _registry()
    result = r.invoke(
        ToolKind.PAYMENTS,
        "authorize",
        {"tier": SpendTier.GREEN, "budget_check_passed": True},
        household_id=1,
        amount_inr=100,
        tier=SpendTier.GREEN,
    )
    assert result["executed"] is True


def test_voice_refused_for_non_cook_facing_message():
    r = _registry()
    with pytest.raises(ToolCallRefused):
        r.invoke(ToolKind.VOICE, "reply", {"cook_facing": False})


def test_voice_allowed_for_cook_facing_message():
    r = _registry()
    result = r.invoke(ToolKind.VOICE, "reply", {"cook_facing": True})
    assert result["text"] == "ok"


def test_no_provider_registered_raises_runtime_error():
    r = ToolRegistry()
    with pytest.raises(RuntimeError):
        r.invoke(ToolKind.LOGISTICS, "validate_address", {"order_under_consideration": True})

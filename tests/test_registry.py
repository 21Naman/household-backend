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
    """Above green with no asserted human approval, the gate still refuses.

    The context here has no `human_approval_verified` key at all, which is
    the important half: a caller that has not been updated to assert it is
    refused rather than admitted.
    """
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


def test_payments_refused_above_green_when_the_approval_flag_is_false():
    """Explicitly false must read the same as absent."""
    r = _registry()
    with pytest.raises(ToolCallRefused, match="verified human approval"):
        r.invoke(
            ToolKind.PAYMENTS,
            "authorize",
            {
                "tier": SpendTier.RED,
                "budget_check_passed": True,
                "human_approval_verified": False,
            },
            household_id=1,
            amount_inr=2000,
            tier=SpendTier.RED,
        )


def test_payments_allowed_above_green_with_a_verified_human_approval():
    """The branch that gives the approval gate somewhere to lead.

    Only the deterministic route layer sets this, and only after
    check_execution_authorized has confirmed an APPROVED request whose
    recorded amount still matches the basket in hand.
    """
    r = _registry()
    result = r.invoke(
        ToolKind.PAYMENTS,
        "authorize",
        {
            "tier": SpendTier.RED,
            "budget_check_passed": True,
            "human_approval_verified": True,
        },
        household_id=1,
        amount_inr=2000,
        tier=SpendTier.RED,
    )
    assert result == {"executed": True}
    assert r.blocked_calls.entries == []


def test_a_verified_approval_does_not_buy_the_right_to_break_the_budget():
    """The budget check is evaluated first on purpose: it applies at every
    tier, and approving a basket is not the same as raising the limit."""
    r = _registry()
    with pytest.raises(ToolCallRefused, match="budget check"):
        r.invoke(
            ToolKind.PAYMENTS,
            "authorize",
            {
                "tier": SpendTier.RED,
                "budget_check_passed": False,
                "human_approval_verified": True,
            },
            household_id=1,
            amount_inr=2000,
            tier=SpendTier.RED,
        )


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

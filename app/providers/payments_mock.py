"""Ticket #18 — mock tiered payment authorizer.

The original codebase's only payment path (zepto_mcp.create_order_payment)
returns a UPI deep link / checkout URL: a human taps to pay on every order
regardless of tier, which quietly cancels tiered autonomy. This mock
implements the model Bible §3.1's Pine Labs correction actually describes:
SBMD/OTM lets a household reserve a ceiling ONCE, after which the agent can
debit variable amounts against it with no further authentication -- until
the ceiling runs out or the tier stops being green.

This mock and Ticket #40's live Pine Labs provider share one contract
(app.core.interfaces.PaymentProvider). Tier classification is never done
here -- it is received from app.services.classify_order_tier and trusted
only because app.core.registry's gate already re-checked it.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.enums import SpendTier
from app.models import PineLabsConnection


@dataclass
class PaymentResult:
    executed: bool
    requires_human: bool
    reference: str | None
    reason: str


class MockPaymentProvider:
    """In-memory / DB-backed mock. No network call is ever made -- assert
    this in tests (Ticket #18's acceptance criteria)."""

    def authorize(self, connection: PineLabsConnection | None, amount_inr: float, tier: SpendTier) -> PaymentResult:
        if tier != SpendTier.GREEN:
            return PaymentResult(
                executed=False,
                requires_human=True,
                reference=None,
                reason=f"{tier.value} tier requires human approval before any payment call is made",
            )

        if connection is None:
            return PaymentResult(
                executed=False,
                requires_human=True,
                reference=None,
                reason="no reserved ceiling on file; treating unset ceiling as zero (fail safe)",
            )

        remaining_ceiling = connection.reserved_ceiling_inr - connection.ceiling_used_inr
        if amount_inr > remaining_ceiling:
            return PaymentResult(
                executed=False,
                requires_human=True,
                reference=None,
                reason=f"₹{amount_inr:.2f} exceeds remaining reserved ceiling ₹{remaining_ceiling:.2f}",
            )

        connection.ceiling_used_inr += amount_inr
        return PaymentResult(
            executed=True,
            requires_human=False,
            reference=f"mock-p3p-{connection.household_id}-{int(connection.ceiling_used_inr * 100)}",
            reason=f"green tier debited ₹{amount_inr:.2f} against reserved ceiling with no further authentication",
        )

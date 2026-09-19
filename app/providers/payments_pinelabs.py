"""Ticket #40 -- Pine Labs P3P/Grantex (live). BLOCKED on RQ4/RQ5.

RQ4: does SBMD/OTM support variable per-item pricing for loose produce
within one household ceiling at a LIVE checkout?
RQ5: what is Grantex's actual UX for a HOUSEHOLD (not individual) mandate?

Both are open per the Bible's Assumption Register. Building against an
assumed mandate shape wastes the work if the shape is different -- so
this module is implemented against the P3P/Grantex API as documented, but
deliberately refuses to run live unless the caller passes
acknowledge_unvalidated_assumptions=True, so nobody accidentally exercises
unvalidated assumptions against a real household's money.

Bible §3.1 contradicts the team's own B037 claim that 2FA fires on
variable-weight produce. If a live sandbox test DOES trigger 2FA, that
becomes the honest Q5 "where it breaks" answer -- either result from
running this against a real sandbox is usable; assuming one without
testing is the only losing move.

Highest-risk integration in the system: credentials come only from
Secrets Manager (app.providers.secrets), never inline; the Lambda role
that would invoke this is scoped to this one API only (see
infra/cdk/tools_stack.py); sandbox-only until RQ4/RQ5 are answered.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.enums import SpendTier


class PineLabsNotYetValidated(RuntimeError):
    """Raised whenever this provider is used without explicitly
    acknowledging that RQ4/RQ5 are unresolved. This is not a bug to fix by
    removing the check -- it is the check."""


class PineLabsError(RuntimeError):
    pass


@dataclass
class PaymentResult:
    executed: bool
    requires_human: bool
    reference: str | None
    reason: str


class PineLabsPaymentProvider:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str,
        timeout_seconds: float = 20.0,
        acknowledge_unvalidated_assumptions: bool = False,
    ):
        if not acknowledge_unvalidated_assumptions:
            raise PineLabsNotYetValidated(
                "Pine Labs P3P/Grantex is BLOCKED on RQ4 (variable-pricing support under one "
                "household ceiling) and RQ5 (Grantex's household-mandate UX). Run the sandbox "
                "test described in the build map's Ticket #40 first; pass "
                "acknowledge_unvalidated_assumptions=True only once you have real sandbox evidence, "
                "and record whichever answer you got (works, or 2FA fires) in the submission."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def authorize(self, mandate_token: str, amount_inr: float, tier: SpendTier) -> PaymentResult:
        """Tier is received, never computed here (app.services owns
        classification) -- and app.core.registry's gate has already
        refused this call for anything but tier=green before it reaches
        here in the real request flow."""
        if tier != SpendTier.GREEN:
            return PaymentResult(False, True, None, f"{tier.value} tier requires human approval before any payment call")
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/p3p/debit",
                    json={"mandate_token": mandate_token, "amount_inr": amount_inr},
                    headers={"Authorization": f"Bearer {self.client_id}:{self.client_secret}"},
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("requires_2fa"):
                    # This is exactly the live test of the Bible §3.1
                    # correction vs. the original B037 claim -- record
                    # whichever answer comes back.
                    return PaymentResult(False, True, None, "Pine Labs requested 2FA -- B037's original concern was confirmed live, not the §3.1 correction")
                return PaymentResult(True, False, payload.get("reference"), "executed against reserved household ceiling")
        except httpx.HTTPError as exc:
            raise PineLabsError(f"Pine Labs P3P debit failed: {exc}") from exc

"""Gated tool registry — Ticket #5.

This module is the ONLY path to a tool call in the whole system. A model
response may *request* a tool; it never *invokes* one directly. Every call
here passes through a deterministic predicate before the underlying
provider is touched. This is the chokepoint that stops an LLM from
triggering spend, and it is why app/core/registry is treated as
security-relevant — see Ticket #12 for the approval gate this composes with.

Gating rules (Bible §4.3 "Tool-selection logic"):
  - voice fires only for cook-facing messages, and speech synthesis is
    additionally capped per household per day -- a call that spends vendor
    credits is spend, and spend goes through this gate like every other
    kind
  - payments fire only after classify_order_tier returns GREEN AND a
    deterministic budget check passes (checked again at execution time,
    not just at proposal time — see Ticket #12)
  - logistics fires only when an order is actually under consideration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Callable

from app.enums import SpendTier


class ToolKind(str, Enum):
    VOICE = "voice"
    LOGISTICS = "logistics"
    PAYMENTS = "payments"
    COMMERCE = "commerce"


class ToolCallRefused(RuntimeError):
    """Raised when the registry's gate refuses a call. This is a normal,
    expected control-flow outcome, not a bug — callers must handle it."""

    def __init__(self, tool: ToolKind, reason: str):
        self.tool = tool
        self.reason = reason
        super().__init__(f"Tool call to {tool.value} refused: {reason}")


@dataclass
class BlockedCallLog:
    """Every refusal is recorded, not just raised, so it can be audited
    (Ticket #28 asserts these surface in the audit trail)."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, tool: ToolKind, reason: str, context: dict[str, Any]) -> None:
        self.entries.append({"tool": tool.value, "reason": reason, "context": context})


class ToolRegistry:
    """Construct once per app, inject providers, call `invoke`. Never call
    a provider directly from a route handler or from app/services.py."""

    def __init__(self, daily_synthesis_limit: int | None = None) -> None:
        self._providers: dict[ToolKind, Any] = {}
        self.blocked_calls = BlockedCallLog()
        self.daily_synthesis_limit = daily_synthesis_limit
        # (household_id, UTC date) -> count. In-process and therefore
        # per-worker, exactly like app/core/recipe_audio_cache.py; both move
        # to a shared store together when this is deployed multi-worker.
        self._synthesis_counts: dict[tuple[int, date], int] = {}
        self._counts_lock = Lock()

    def register(self, kind: ToolKind, provider: Any) -> None:
        self._providers[kind] = provider

    # -- gates -----------------------------------------------------------

    def _gate_voice(self, context: dict[str, Any], method: str) -> tuple[bool, str]:
        if not context.get("cook_facing"):
            return False, "voice tool requested for a non-cook-facing message"
        if method != "synthesize":
            return True, ""
        household_id = context.get("household_id")
        if not household_id:
            return False, "speech synthesis requires a household_id in context"
        if self.daily_synthesis_limit is None:
            return True, ""
        used = self._synthesis_count(household_id)
        if used >= self.daily_synthesis_limit:
            return False, (
                f"speech synthesis daily limit reached for this household "
                f"({used}/{self.daily_synthesis_limit})"
            )
        return True, ""

    def _synthesis_count(self, household_id: int) -> int:
        today = datetime.now(timezone.utc).date()
        with self._counts_lock:
            self._prune_counts(today)
            return self._synthesis_counts.get((household_id, today), 0)

    def _record_synthesis(self, household_id: int) -> None:
        """Counted when the gate opens, not when the vendor answers. A call
        that is attempted and fails still consumed an attempt, and counting
        optimistically here keeps the cap fail-closed under concurrency."""
        today = datetime.now(timezone.utc).date()
        with self._counts_lock:
            self._prune_counts(today)
            self._synthesis_counts[(household_id, today)] = (
                self._synthesis_counts.get((household_id, today), 0) + 1
            )

    def _prune_counts(self, today: date) -> None:
        """Caller must hold the lock. Yesterday's counters are dead weight."""
        for key in [key for key in self._synthesis_counts if key[1] != today]:
            del self._synthesis_counts[key]

    def _gate_logistics(self, context: dict[str, Any], method: str) -> tuple[bool, str]:
        if not context.get("order_under_consideration"):
            return False, "logistics tool requested with no order under consideration"
        return True, ""

    def _gate_payments(self, context: dict[str, Any], method: str) -> tuple[bool, str]:
        """Green tier pays unattended; above it, a verified human decision is
        required before money moves.

        The budget check comes first deliberately: it applies at every tier,
        and an approval does not buy the right to exceed the budget.

        `human_approval_verified` is asserted only by the deterministic route
        layer, and only after check_execution_authorized has confirmed an
        APPROVED request whose recorded amount still matches the current
        basket. It is read with .get() so a caller that has not been updated
        to set it is refused rather than admitted -- this gate fails closed,
        like every other decision in this system.

        Before this branch existed the gate refused every non-green tier
        outright, which meant an approved yellow order passed the approval
        check and was then blocked here: the approval gate had no path to a
        completed purchase at all.
        """
        tier = context.get("tier")
        if not context.get("budget_check_passed"):
            return False, "payments tool requires a passing deterministic budget check"
        if tier == SpendTier.GREEN:
            return True, ""
        if context.get("human_approval_verified"):
            return True, ""
        return False, f"payments tool requires tier=green or a verified human approval, got {tier!r}"

    def _gate_commerce(self, context: dict[str, Any], method: str) -> tuple[bool, str]:
        # Commerce (search/quote) is read-only and low-risk; gated only on
        # having a household context at all, so a stray call can't leak
        # cross-household catalog behavior.
        if not context.get("household_id"):
            return False, "commerce tool requires a household_id in context"
        return True, ""

    _GATES: dict[ToolKind, str] = {
        ToolKind.VOICE: "_gate_voice",
        ToolKind.LOGISTICS: "_gate_logistics",
        ToolKind.PAYMENTS: "_gate_payments",
        ToolKind.COMMERCE: "_gate_commerce",
    }

    def invoke(self, kind: ToolKind, method: str, context: dict[str, Any], *args, **kwargs) -> Any:
        """The single call surface. `context` carries whatever the gate for
        `kind` needs (see the _gate_* methods above) — callers must build it
        explicitly rather than the registry inferring it, so a missing
        context key fails closed instead of silently passing.

        `method` reaches the gate as well as the provider, because some
        gates care which method is being called: voice replies are free,
        voice *synthesis* spends vendor credits and is capped."""
        gate_name = self._GATES[kind]
        allowed, reason = getattr(self, gate_name)(context, method)
        if not allowed:
            self.blocked_calls.record(kind, reason, context)
            raise ToolCallRefused(kind, reason)

        provider = self._providers.get(kind)
        if provider is None:
            raise RuntimeError(f"No provider registered for tool kind {kind.value}")
        if kind is ToolKind.VOICE and method == "synthesize":
            self._record_synthesis(context["household_id"])
        fn: Callable = getattr(provider, method)
        return fn(*args, **kwargs)

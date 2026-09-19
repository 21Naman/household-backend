"""Gated tool registry — Ticket #5.

This module is the ONLY path to a tool call in the whole system. A model
response may *request* a tool; it never *invokes* one directly. Every call
here passes through a deterministic predicate before the underlying
provider is touched. This is the chokepoint that stops an LLM from
triggering spend, and it is why app/core/registry is treated as
security-relevant — see Ticket #12 for the approval gate this composes with.

Gating rules (Bible §4.3 "Tool-selection logic"):
  - voice fires only for cook-facing messages
  - payments fire only after classify_order_tier returns GREEN AND a
    deterministic budget check passes (checked again at execution time,
    not just at proposal time — see Ticket #12)
  - logistics fires only when an order is actually under consideration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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

    def __init__(self) -> None:
        self._providers: dict[ToolKind, Any] = {}
        self.blocked_calls = BlockedCallLog()

    def register(self, kind: ToolKind, provider: Any) -> None:
        self._providers[kind] = provider

    # -- gates -----------------------------------------------------------

    def _gate_voice(self, context: dict[str, Any]) -> tuple[bool, str]:
        if not context.get("cook_facing"):
            return False, "voice tool requested for a non-cook-facing message"
        return True, ""

    def _gate_logistics(self, context: dict[str, Any]) -> tuple[bool, str]:
        if not context.get("order_under_consideration"):
            return False, "logistics tool requested with no order under consideration"
        return True, ""

    def _gate_payments(self, context: dict[str, Any]) -> tuple[bool, str]:
        tier = context.get("tier")
        budget_check_passed = context.get("budget_check_passed")
        if tier != SpendTier.GREEN:
            return False, f"payments tool requires tier=green, got {tier!r}"
        if not budget_check_passed:
            return False, "payments tool requires a passing deterministic budget check"
        return True, ""

    def _gate_commerce(self, context: dict[str, Any]) -> tuple[bool, str]:
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
        context key fails closed instead of silently passing."""
        gate_name = self._GATES[kind]
        allowed, reason = getattr(self, gate_name)(context)
        if not allowed:
            self.blocked_calls.record(kind, reason, context)
            raise ToolCallRefused(kind, reason)

        provider = self._providers.get(kind)
        if provider is None:
            raise RuntimeError(f"No provider registered for tool kind {kind.value}")
        fn: Callable = getattr(provider, method)
        return fn(*args, **kwargs)

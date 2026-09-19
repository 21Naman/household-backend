"""Typed state vocabulary — Ticket #2.

Every tier and status in the system used to be a free `str` field. That meant
a typo like ``"awaiting_aproval"`` would silently write a corrupted record
and the route would still return 200. These enums are the single source of
truth for every value that used to be a string literal scattered across
models.py, schemas.py, scheduler.py and seed.py.

Values below cover every status string already present in seed.py's demo
scenarios (recorded, triggered, planned, awaiting_approval, cooking,
completed) plus "unclosed" for Ticket #24's timeout sweep.
"""

from __future__ import annotations

from enum import Enum


class SpendTier(str, Enum):
    """Ticket #11's classifier output. Fail-closed: an unrecognized or
    unclassifiable situation must resolve to RED, never GREEN."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class LoopStatus(str, Enum):
    """MealLoopRecord.status. UNCLOSED is not a failure state the loop can
    be posted into directly — it is written only by Ticket #24's sweep."""

    RECORDED = "recorded"
    TRIGGERED = "triggered"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    COOKING = "cooking"
    COMPLETED = "completed"
    UNCLOSED = "unclosed"

    @classmethod
    def terminal(cls) -> set["LoopStatus"]:
        """States after which the loop is considered closed and the sweep
        in Ticket #24 should no longer touch it."""
        return {cls.COMPLETED, cls.UNCLOSED}


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProcurementPath(str, Enum):
    """Ticket #21's consolidator output — which path was chosen to close
    an ingredient gap."""

    ALREADY_STOCKED = "already_stocked"
    TOP_UP_ORDER = "top_up_order"
    MANUAL_PURCHASE = "manual_purchase"
    ESCALATED = "escalated"  # no affordable/feasible path — red-tier approval


class FreshnessState(str, Enum):
    """Ticket #9's reconciled freshness authority. Distinct from the raw
    InventoryLot.freshness field, which is only the visual-capture input."""

    FRESH = "fresh"
    EXPIRES_TODAY = "expires_today"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"
    USE_IMMEDIATELY = "use_immediately"
    STALE = "stale"  # not updated within the recency window
    UNKNOWN = "unknown"

    @property
    def usable(self) -> bool:
        """Whether a lot in this state counts toward stock for Ticket #10's
        gap calculator. Conservative by design — STALE and UNKNOWN do not
        count, matching Ticket #9's "fail safe toward asking the human"."""
        return self in {FreshnessState.FRESH, FreshnessState.EXPIRES_TODAY, FreshnessState.EXPIRING_SOON}

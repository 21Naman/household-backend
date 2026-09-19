"""Deterministic decision logic — Phase 1 of the build map (Tickets #9-#12).

Bible §3.6's verdict: order classification, price comparison, and budget
math must be plain code, never a model call — "would add cost and
unpredictability without benefit." This module is the evidence for that
claim. Ticket #13's test suite enforces it mechanically: nothing in this
file may import from app.providers.

Every function here is pure (no I/O, no session, no network) except where
explicitly noted (the approval-gate helpers take a session because they
need to read/write ApprovalRequest rows).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.enums import FreshnessState, LoopStatus, ProcurementPath, SpendTier
from app.models import ApprovalRequest, Budget, Dish, InventoryLot

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Ticket #9 — freshness authority rule
# ---------------------------------------------------------------------------

def expiry_status(expiry_date: date | None, today: date | None = None) -> str:
    """Pure display state, derived purely from dates. It neither consumes
    inventory nor recommends a meal. Kept as the date-only building block
    that effective_freshness() below reconciles against the stored
    (visually-assessed) freshness field."""
    if expiry_date is None:
        return "unknown"
    today = today or date.today()
    if expiry_date < today:
        return "expired"
    if expiry_date == today:
        return "expires_today"
    if (expiry_date - today).days <= 2:
        return "expiring_soon"
    return "fresh"


_DATE_DERIVED_TO_STATE = {
    "expired": FreshnessState.EXPIRED,
    "expires_today": FreshnessState.EXPIRES_TODAY,
    "expiring_soon": FreshnessState.EXPIRING_SOON,
    "fresh": FreshnessState.FRESH,
    "unknown": FreshnessState.UNKNOWN,
}

_VISUAL_STRICTNESS_ORDER = [
    FreshnessState.FRESH,
    FreshnessState.EXPIRES_TODAY,
    FreshnessState.EXPIRING_SOON,
    FreshnessState.USE_IMMEDIATELY,
    FreshnessState.EXPIRED,
]


def _stricter(a: FreshnessState, b: FreshnessState) -> FreshnessState:
    """Among two comparable states, return whichever is more conservative.
    UNKNOWN/STALE are handled by the caller before this is reached."""
    order = {s: i for i, s in enumerate(_VISUAL_STRICTNESS_ORDER)}
    ai = order.get(a, len(order))
    bi = order.get(b, len(order))
    return a if ai >= bi else b


def effective_freshness(
    lot: InventoryLot,
    *,
    today: date | None = None,
    now: datetime | None = None,
    recency_window_hours: int = 48,
) -> FreshnessState:
    """Ticket #9. Reconciles InventoryLot.freshness (a stored VISUAL
    assessment, typically set by the vision-capture pipeline) against the
    date-derived expiry_status(). These can disagree — a lot stored as
    freshness="fresh" with a past expiry_date must not read as fresh.

    Authority rule, in order:
      1. If the lot hasn't been updated within the recency window, it is
         STALE regardless of what either signal says — Bible §4.3's
         verification mechanism requires this.
      2. If neither signal is informative (no expiry_date AND the stored
         freshness is the uninformative default), return UNKNOWN.
      3. Otherwise return the STRICTER (more conservative) of the two
         readings — a visual "use_immediately" overrides a distant expiry
         date, and a past expiry date overrides a visual "fresh".

    This is food-safety-adjacent. Err strict in every ambiguous case.
    """
    now = now or datetime.now(UTC)
    today = today or now.date()

    lot_updated_at = lot.updated_at
    if lot_updated_at is not None:
        if lot_updated_at.tzinfo is None:
            lot_updated_at = lot_updated_at.replace(tzinfo=UTC)
        age = now - lot_updated_at
        if age > timedelta(hours=recency_window_hours):
            return FreshnessState.STALE

    date_state = _DATE_DERIVED_TO_STATE.get(expiry_status(lot.expiry_date, today), FreshnessState.UNKNOWN)

    visual_raw = (lot.freshness or "").strip().lower()
    visual_state = {
        "fresh": FreshnessState.FRESH,
        "expiring_soon": FreshnessState.EXPIRING_SOON,
        "use_immediately": FreshnessState.USE_IMMEDIATELY,
    }.get(visual_raw)

    if date_state == FreshnessState.UNKNOWN and visual_state is None:
        return FreshnessState.UNKNOWN
    if visual_state is None:
        return date_state
    if date_state == FreshnessState.UNKNOWN:
        return visual_state

    return _stricter(date_state, visual_state)


# ---------------------------------------------------------------------------
# Ticket #10 — ingredient-gap calculator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MissingIngredient:
    ingredient: str
    missing_quantity: float
    unit: str
    quantity_unknown: bool = False


# Minimal same-dimension conversion table. Anything not covered here is
# reported as quantity_unknown rather than silently assumed equal — per
# Ticket #10's watch-out, guessing at a unit mismatch is worse than asking.
_MASS_TO_G = {"g": 1.0, "kg": 1000.0}
_VOLUME_TO_ML = {"ml": 1.0, "l": 1000.0, "tsp": 4.9289, "tbsp": 14.7868, "cup": 236.588}


def _convert(quantity: float, from_unit: str, to_unit: str) -> float | None:
    from_unit, to_unit = from_unit.strip().lower(), to_unit.strip().lower()
    if from_unit == to_unit:
        return quantity
    if from_unit in _MASS_TO_G and to_unit in _MASS_TO_G:
        return quantity * _MASS_TO_G[from_unit] / _MASS_TO_G[to_unit]
    if from_unit in _VOLUME_TO_ML and to_unit in _VOLUME_TO_ML:
        return quantity * _VOLUME_TO_ML[from_unit] / _VOLUME_TO_ML[to_unit]
    return None


def compute_ingredient_gap(
    dish: Dish,
    inventory: list[InventoryLot],
    servings: int,
    *,
    today: date | None = None,
    now: datetime | None = None,
    recency_window_hours: int = 48,
) -> list[MissingIngredient]:
    """Ticket #10. Scales dish.ingredients to `servings`, subtracts usable
    stock, returns what's missing. A lot that fails Ticket #9's freshness
    authority (STALE, EXPIRED, UNKNOWN) does not count toward stock."""
    scale = servings / max(dish.servings, 1)

    # Sum usable stock per ingredient name (case-insensitive), keeping each
    # lot's own unit as a candidate conversion target.
    stock_by_ingredient: dict[str, list[tuple[float, str]]] = {}
    for lot in inventory:
        state = effective_freshness(
            lot,
            today=today,
            now=now,
            recency_window_hours=recency_window_hours,
        )
        if not state.usable:
            continue
        key = lot.ingredient.strip().lower()
        stock_by_ingredient.setdefault(key, []).append((lot.quantity, lot.unit))

    gaps: list[MissingIngredient] = []
    for entry in dish.ingredients:
        name = str(entry.get("ingredient", "")).strip()
        required_qty = float(entry.get("quantity", 0)) * scale
        required_unit = str(entry.get("unit", "")).strip()
        if required_qty <= 0 or not name:
            continue

        available = 0.0
        unknown = False
        for qty, unit in stock_by_ingredient.get(name.lower(), []):
            converted = _convert(qty, unit, required_unit)
            if converted is None:
                unknown = True
                continue
            available += converted

        remaining = required_qty - available
        if remaining > 1e-9:
            gaps.append(
                MissingIngredient(
                    ingredient=name,
                    missing_quantity=round(remaining, 3),
                    unit=required_unit,
                    quantity_unknown=unknown,
                )
            )
    return gaps


# ---------------------------------------------------------------------------
# Budget arithmetic — one definition, used by every caller
# ---------------------------------------------------------------------------

def remaining_budget(budget: Budget | None) -> float | None:
    """How much of the monthly limit is still uncommitted, or None when the
    household has no budget on file (which means "unconstrained", not zero).

    This is the single definition of remaining budget in the system. The
    tier classifier, the procurement consolidator, the recipe affordability
    check, and the figure shown to the model in its prompt context must all
    agree — otherwise the model can be told one number while the gate
    enforces another. Never inline this subtraction at a call site.
    """
    if budget is None:
        return None
    return max(budget.monthly_limit - budget.spent_amount - budget.planned_amount, 0.0)


# ---------------------------------------------------------------------------
# Ticket #11 — deterministic spend-tier classifier
# ---------------------------------------------------------------------------

def classify_order_tier(
    amount_inr: float,
    budget: Budget | None,
    *,
    is_routine: bool,
    green_ceiling_inr: float = 300.0,
    red_floor_inr: float = 1500.0,
    unusual_multiplier: float = 2.0,
) -> tuple[SpendTier, str]:
    """Ticket #11. Pure function, no I/O, no model call. This is the
    evidence for the submission's "auditable spend math" claim — it must be
    importable and testable standalone, never embedded in a route handler.

    Ambiguous/edge cases resolve UPWARD to the stricter tier — fail safe
    toward asking the human, never toward autonomous spend.
    """
    if amount_inr < 0:
        return SpendTier.RED, "negative order amount is invalid; escalating"

    remaining = remaining_budget(budget)

    if amount_inr >= red_floor_inr:
        return SpendTier.RED, f"amount ₹{amount_inr:.0f} is at or above the red floor (₹{red_floor_inr:.0f})"

    if not is_routine:
        # An unusual item set gets a stricter effective ceiling.
        effective_ceiling = green_ceiling_inr / unusual_multiplier
        if amount_inr > effective_ceiling:
            return (
                SpendTier.YELLOW,
                f"unusual item set above the reduced ceiling (₹{effective_ceiling:.0f}) for non-routine orders",
            )

    if remaining is not None and amount_inr > remaining:
        return SpendTier.YELLOW, f"amount ₹{amount_inr:.0f} exceeds remaining budget ₹{remaining:.0f}"

    if amount_inr <= green_ceiling_inr:
        return SpendTier.GREEN, f"routine order ₹{amount_inr:.0f} within green ceiling (₹{green_ceiling_inr:.0f}) and budget"

    return SpendTier.YELLOW, f"amount ₹{amount_inr:.0f} exceeds the green ceiling (₹{green_ceiling_inr:.0f})"


# ---------------------------------------------------------------------------
# Ticket #12 — approval gate and loop state machine
# ---------------------------------------------------------------------------

def approval_required(tier: SpendTier) -> bool:
    """Green never requires approval. Yellow and red always do — the
    distinction between them is who gets notified and how urgently, not
    whether a human is in the loop at all."""
    return tier != SpendTier.GREEN


def check_execution_authorized(
    approval: ApprovalRequest | None,
    tier: SpendTier,
    current_amount_inr: float,
) -> tuple[bool, str]:
    """Ticket #12's core guarantee, checked at EXECUTION time, not just at
    proposal time. A stale approval — one granted for a different basket
    total — must not authorize a changed basket.

    Fail closed: an unknown/unclassifiable tier is treated as red.
    """
    if not approval_required(tier):
        return True, "green tier executes without approval"

    if approval is None:
        return False, f"{tier.value} tier requires a recorded approval; none found"

    from app.enums import ApprovalStatus  # local import avoids a cycle at module load

    if approval.status != ApprovalStatus.APPROVED:
        return False, f"approval status is {approval.status.value}, not approved"

    if approval.approved_amount_inr is None:
        return False, "approval has no recorded amount snapshot; cannot verify basket has not changed"

    if abs(approval.approved_amount_inr - current_amount_inr) > 0.01:
        return (
            False,
            f"basket changed since approval (approved ₹{approval.approved_amount_inr:.2f}, "
            f"now ₹{current_amount_inr:.2f}) — stale approval refused",
        )

    return True, "approved amount matches current basket"


# ---------------------------------------------------------------------------
# Ticket #21. Quotes are gathered in the route layer (they need
# CommerceProvider calls), but the pure decision core — choosing a
# procurement path from quotes already in hand — belongs here to keep the
# LLM-free boundary in one file.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderQuote:
    provider_name: str
    total_inr: float
    feasible: bool  # False if the provider can't fulfill the full basket


def consolidate_orders(
    gap: list[MissingIngredient],
    quotes: list[ProviderQuote],
    budget: Budget | None,
    delivery_confidence: float,
    *,
    confidence_threshold: float = 0.4,
) -> tuple[ProcurementPath, str, ProviderQuote | None]:
    """Ticket #21. Deterministic optimization only — no model call chooses
    the path. Consolidates to as few orders as possible (B020) by picking
    the single cheapest feasible provider rather than splitting the basket.
    """
    if not gap:
        return ProcurementPath.ALREADY_STOCKED, "no missing ingredients", None

    if delivery_confidence < confidence_threshold:
        return (
            ProcurementPath.MANUAL_PURCHASE,
            f"delivery confidence {delivery_confidence:.2f} below threshold {confidence_threshold:.2f}; "
            "routing to manual purchase per B008",
            None,
        )

    feasible = [q for q in quotes if q.feasible]
    if not feasible:
        return ProcurementPath.ESCALATED, "no provider can fulfill the full basket", None

    cheapest = min(feasible, key=lambda q: q.total_inr)

    remaining = remaining_budget(budget)
    if remaining is not None and cheapest.total_inr > remaining:
        # Doesn't fit budget even at the cheapest feasible price — never
        # silently over-order. Escalate for human review with the reason.
        return (
            ProcurementPath.ESCALATED,
            f"cheapest feasible basket ₹{cheapest.total_inr:.2f} exceeds remaining budget ₹{remaining:.2f}",
            cheapest,
        )

    price_note = ""
    others = [q for q in feasible if q.provider_name != cheapest.provider_name]
    if others:
        delta = min(o.total_inr for o in others) - cheapest.total_inr
        price_note = f"; ₹{delta:.2f} cheaper than the next best provider"

    return (
        ProcurementPath.TOP_UP_ORDER,
        f"{cheapest.provider_name} selected at ₹{cheapest.total_inr:.2f}{price_note}",
        cheapest,
    )

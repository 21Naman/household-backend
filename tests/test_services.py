from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.enums import ApprovalStatus, FreshnessState, ProcurementPath, SpendTier
from app.models import ApprovalRequest, Budget, Dish, InventoryLot
from app.services import (
    ProviderQuote,
    approval_required,
    check_execution_authorized,
    classify_order_tier,
    compute_ingredient_gap,
    consolidate_orders,
    effective_freshness,
    expiry_status,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Import-boundary test — the mechanical enforcement of Bible §3.6.
# ---------------------------------------------------------------------------

def test_services_never_imports_a_provider():
    """This is the test that backs the submission's claim that spend math
    and classification are auditable code, not model output. If this ever
    fails, someone routed a deterministic decision through an LLM call."""
    source = Path(__file__).resolve().parents[1].joinpath("app", "services.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "app.providers" not in alias.name, f"services.py imports a provider: {alias.name}"
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert "app.providers" not in module, f"services.py imports from a provider module: {module}"


# ---------------------------------------------------------------------------
# Ticket #9 — freshness authority, four-case truth table + staleness
# ---------------------------------------------------------------------------

def _lot(*, freshness: str, expiry_date, updated_at=None) -> InventoryLot:
    return InventoryLot(
        household_id=1,
        ingredient="Test",
        quantity=1,
        unit="g",
        freshness=freshness,
        expiry_date=expiry_date,
        updated_at=updated_at or datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "visual,expiry_offset_days,expected",
    [
        ("fresh", 10, FreshnessState.FRESH),  # both fresh -> fresh
        ("fresh", -1, FreshnessState.EXPIRED),  # visual fresh, date expired -> stricter wins (expired)
        ("use_immediately", 10, FreshnessState.USE_IMMEDIATELY),  # visual strict, date fine -> stricter wins
        ("use_immediately", -1, FreshnessState.EXPIRED),  # both strict -> expired is stricter than use_immediately
    ],
)
def test_effective_freshness_truth_table(visual, expiry_offset_days, expected):
    today = date(2026, 6, 1)
    lot = _lot(freshness=visual, expiry_date=today + timedelta(days=expiry_offset_days))
    assert effective_freshness(lot, today=today) == expected


def test_effective_freshness_stale_beyond_recency_window():
    lot = _lot(freshness="fresh", expiry_date=date(2026, 12, 1), updated_at=datetime.now(UTC) - timedelta(hours=100))
    assert effective_freshness(lot, recency_window_hours=48) == FreshnessState.STALE


def test_effective_freshness_naive_updated_at_is_treated_as_utc():
    lot = _lot(freshness="fresh", expiry_date=date(2026, 12, 1))
    lot.updated_at = datetime.now(UTC).replace(tzinfo=None)  # naive
    assert effective_freshness(lot, recency_window_hours=48) == FreshnessState.FRESH


def test_effective_freshness_visual_unknown_falls_back_to_date_state():
    lot = _lot(freshness="", expiry_date=date(2026, 1, 1))
    assert effective_freshness(lot, today=date(2026, 1, 1)) == FreshnessState.EXPIRES_TODAY


def test_effective_freshness_date_unknown_falls_back_to_visual_state():
    lot = _lot(freshness="use_immediately", expiry_date=None)
    assert effective_freshness(lot) == FreshnessState.USE_IMMEDIATELY


def test_effective_freshness_unknown_when_no_signal():
    lot = InventoryLot(household_id=1, ingredient="Test", quantity=1, unit="g", freshness="", expiry_date=None)
    lot.updated_at = datetime.now(UTC)
    assert effective_freshness(lot) == FreshnessState.UNKNOWN


def test_expiry_status_unchanged_pure_function():
    today = date(2026, 6, 1)
    assert expiry_status(None, today) == "unknown"
    assert expiry_status(today, today) == "expires_today"
    assert expiry_status(today - timedelta(days=1), today) == "expired"
    assert expiry_status(today + timedelta(days=1), today) == "expiring_soon"
    assert expiry_status(today + timedelta(days=10), today) == "fresh"


# ---------------------------------------------------------------------------
# Ticket #10 — ingredient gap, run against a cook_mishap-style fixture
# ---------------------------------------------------------------------------

def test_ingredient_gap_cook_mishap_scenario():
    """Mirrors seed.py's cook_mishap scenario: paneer quantity zeroed,
    moong dal stocked. Paneer Bhurji's gap should list paneer; Vegetable
    Khichdi's gap (using moong dal, in stock) should be empty."""
    today = date(2026, 6, 1)
    inventory = [
        _lot(freshness="fresh", expiry_date=today + timedelta(days=5)),
    ]
    inventory[0].ingredient = "Moong Dal"
    inventory[0].quantity = 500
    inventory[0].unit = "g"

    paneer_bhurji = Dish(
        name="Paneer Bhurji",
        servings=2,
        ingredients=[{"ingredient": "Paneer", "quantity": 200, "unit": "g"}],
    )
    khichdi = Dish(
        name="Vegetable Khichdi",
        servings=2,
        ingredients=[{"ingredient": "Moong Dal", "quantity": 150, "unit": "g"}],
    )

    gap_paneer = compute_ingredient_gap(paneer_bhurji, inventory, servings=2, today=today)
    assert any(g.ingredient == "Paneer" for g in gap_paneer)

    gap_khichdi = compute_ingredient_gap(khichdi, inventory, servings=2, today=today)
    assert gap_khichdi == []


def test_ingredient_gap_mass_and_volume_conversion():
    today = date(2026, 6, 1)
    kg_lot = _lot(freshness="fresh", expiry_date=today + timedelta(days=5))
    kg_lot.ingredient, kg_lot.quantity, kg_lot.unit = "Rice", 1, "kg"
    l_lot = _lot(freshness="fresh", expiry_date=today + timedelta(days=5))
    l_lot.ingredient, l_lot.quantity, l_lot.unit = "Milk", 1, "l"

    rice_dish = Dish(name="Rice", servings=1, ingredients=[{"ingredient": "Rice", "quantity": 500, "unit": "g"}])
    milk_dish = Dish(name="Milk", servings=1, ingredients=[{"ingredient": "Milk", "quantity": 500, "unit": "ml"}])

    assert compute_ingredient_gap(rice_dish, [kg_lot], servings=1, today=today) == []
    assert compute_ingredient_gap(milk_dish, [l_lot], servings=1, today=today) == []


def test_ingredient_gap_skips_zero_quantity_and_unnamed_entries():
    today = date(2026, 6, 1)
    dish = Dish(
        name="Odd",
        servings=1,
        ingredients=[
            {"ingredient": "Salt", "quantity": 0, "unit": "g"},
            {"ingredient": "", "quantity": 5, "unit": "g"},
        ],
    )
    assert compute_ingredient_gap(dish, [], servings=1, today=today) == []


def test_ingredient_gap_scales_with_servings():
    today = date(2026, 6, 1)
    dish = Dish(name="Dal", servings=2, ingredients=[{"ingredient": "Toor Dal", "quantity": 100, "unit": "g"}])
    gap = compute_ingredient_gap(dish, [], servings=4, today=today)
    assert gap[0].missing_quantity == 200


def test_ingredient_gap_unit_mismatch_is_unknown_not_guessed():
    today = date(2026, 6, 1)
    lot = _lot(freshness="fresh", expiry_date=today + timedelta(days=5))
    lot.ingredient = "Rice"
    lot.quantity = 3
    lot.unit = "piece"  # nonsensical unit for rice on purpose
    dish = Dish(name="Rice", servings=1, ingredients=[{"ingredient": "Rice", "quantity": 200, "unit": "g"}])
    gap = compute_ingredient_gap(dish, [lot], servings=1, today=today)
    assert gap and gap[0].quantity_unknown is True


def test_ingredient_gap_expired_lot_does_not_count_as_stock():
    today = date(2026, 6, 1)
    lot = _lot(freshness="fresh", expiry_date=today - timedelta(days=2))
    lot.ingredient = "Milk"
    lot.quantity = 1000
    lot.unit = "ml"
    dish = Dish(name="Kheer", servings=1, ingredients=[{"ingredient": "Milk", "quantity": 500, "unit": "ml"}])
    gap = compute_ingredient_gap(dish, [lot], servings=1, today=today)
    assert gap and gap[0].missing_quantity == 500


# ---------------------------------------------------------------------------
# Ticket #11 — spend-tier classifier, 12+ table-driven cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "amount,monthly_limit,spent,planned,is_routine,expected_tier",
    [
        (100, 2000, 500, 0, True, SpendTier.GREEN),
        (300, 2000, 500, 0, True, SpendTier.GREEN),  # exactly at green ceiling
        (301, 2000, 500, 0, True, SpendTier.YELLOW),  # just above ceiling
        (1500, 2000, 500, 0, True, SpendTier.RED),  # at red floor
        (2000, 2000, 500, 0, True, SpendTier.RED),  # well above
        (0, 2000, 500, 0, True, SpendTier.GREEN),  # zero order
        (200, 2000, 1950, 0, True, SpendTier.YELLOW),  # exceeds remaining budget
        (137, 227, 137, 0, True, SpendTier.YELLOW),  # budget_constraint seed case: ₹137 vs ₹90 remaining
        (100, 2000, 500, 0, False, SpendTier.GREEN),  # unusual, but under reduced ceiling (300/2=150)
        (160, 2000, 500, 0, False, SpendTier.YELLOW),  # unusual, above reduced ceiling of 150
        (100, None, 0, 0, True, SpendTier.GREEN),  # no budget row at all
        (-5, 2000, 0, 0, True, SpendTier.RED),  # invalid negative amount fails closed to red
    ],
)
def test_classify_order_tier_table(amount, monthly_limit, spent, planned, is_routine, expected_tier):
    budget = None
    if monthly_limit is not None:
        budget = Budget(household_id=1, monthly_limit=monthly_limit, spent_amount=spent, planned_amount=planned)
    tier, reason = classify_order_tier(amount, budget, is_routine=is_routine)
    assert tier == expected_tier, reason


def test_classify_order_tier_budget_constraint_seed_case_explicit():
    """₹137 basket against ₹90 remaining (227 limit, 137 already committed)
    must resolve to yellow, not green and not red."""
    budget = Budget(household_id=1, monthly_limit=227, spent_amount=137, planned_amount=0)
    tier, _ = classify_order_tier(137, budget, is_routine=True)
    assert tier == SpendTier.YELLOW


# ---------------------------------------------------------------------------
# Ticket #12 — approval gate, including the stale-approval case
# ---------------------------------------------------------------------------

def test_green_never_requires_approval():
    ok, reason = check_execution_authorized(None, SpendTier.GREEN, 250.0)
    assert ok is True


def test_red_with_no_approval_is_refused():
    ok, reason = check_execution_authorized(None, SpendTier.RED, 2000.0)
    assert ok is False


def test_stale_approval_refused_when_basket_changed():
    """The bug this ticket exists to prevent: approve a ₹137 basket, then
    try to execute a ₹400 basket under the same approval."""
    approval = ApprovalRequest(
        household_id=1,
        meal_loop_id=1,
        tier=SpendTier.YELLOW,
        action="order",
        amount_inr=137,
        approved_amount_inr=137,
        status=ApprovalStatus.APPROVED,
        reason="over green ceiling",
    )
    ok, reason = check_execution_authorized(approval, SpendTier.YELLOW, 400.0)
    assert ok is False
    assert "stale" in reason.lower() or "changed" in reason.lower()


def test_matching_approval_authorizes_execution():
    approval = ApprovalRequest(
        household_id=1,
        meal_loop_id=1,
        tier=SpendTier.YELLOW,
        action="order",
        amount_inr=137,
        approved_amount_inr=137,
        status=ApprovalStatus.APPROVED,
        reason="over green ceiling",
    )
    ok, _ = check_execution_authorized(approval, SpendTier.YELLOW, 137.0)
    assert ok is True


def test_approval_required_true_for_yellow_and_red_false_for_green():
    assert approval_required(SpendTier.GREEN) is False
    assert approval_required(SpendTier.YELLOW) is True
    assert approval_required(SpendTier.RED) is True


def test_approved_with_no_amount_snapshot_refused():
    approval = ApprovalRequest(
        household_id=1,
        meal_loop_id=1,
        tier=SpendTier.YELLOW,
        action="order",
        amount_inr=137,
        approved_amount_inr=None,
        status=ApprovalStatus.APPROVED,
        reason="over green ceiling",
    )
    ok, reason = check_execution_authorized(approval, SpendTier.YELLOW, 137.0)
    assert ok is False
    assert "snapshot" in reason


def test_unapproved_status_refused_even_if_amount_matches():
    approval = ApprovalRequest(
        household_id=1,
        meal_loop_id=1,
        tier=SpendTier.YELLOW,
        action="order",
        amount_inr=137,
        approved_amount_inr=137,
        status=ApprovalStatus.PENDING,
        reason="over green ceiling",
    )
    ok, _ = check_execution_authorized(approval, SpendTier.YELLOW, 137.0)
    assert ok is False


# ---------------------------------------------------------------------------
# Ticket #21's deterministic core — consolidation
# ---------------------------------------------------------------------------

def test_consolidate_orders_picks_cheapest_feasible():
    from app.services import MissingIngredient

    gap = [MissingIngredient(ingredient="Paneer", missing_quantity=200, unit="g")]
    quotes = [
        ProviderQuote("Zepto", total_inr=120, feasible=True),
        ProviderQuote("Blinkit", total_inr=95, feasible=True),
    ]
    budget = Budget(household_id=1, monthly_limit=2000, spent_amount=0, planned_amount=0)
    path, reason, chosen = consolidate_orders(gap, quotes, budget, delivery_confidence=0.9)
    assert path == ProcurementPath.TOP_UP_ORDER
    assert chosen.provider_name == "Blinkit"


def test_consolidate_orders_low_confidence_routes_manual():
    from app.services import MissingIngredient

    gap = [MissingIngredient(ingredient="Paneer", missing_quantity=200, unit="g")]
    quotes = [ProviderQuote("Zepto", total_inr=95, feasible=True)]
    path, _, _ = consolidate_orders(gap, quotes, None, delivery_confidence=0.1)
    assert path == ProcurementPath.MANUAL_PURCHASE


def test_consolidate_orders_no_feasible_provider_escalates():
    from app.services import MissingIngredient

    gap = [MissingIngredient(ingredient="Paneer", missing_quantity=200, unit="g")]
    quotes = [ProviderQuote("Zepto", total_inr=95, feasible=False)]
    path, _, _ = consolidate_orders(gap, quotes, None, delivery_confidence=0.9)
    assert path == ProcurementPath.ESCALATED


def test_consolidate_orders_over_budget_escalates_never_overspends():
    from app.services import MissingIngredient

    gap = [MissingIngredient(ingredient="Paneer", missing_quantity=200, unit="g")]
    quotes = [ProviderQuote("Zepto", total_inr=500, feasible=True)]
    budget = Budget(household_id=1, monthly_limit=100, spent_amount=90, planned_amount=0)
    path, reason, chosen = consolidate_orders(gap, quotes, budget, delivery_confidence=0.9)
    assert path == ProcurementPath.ESCALATED
    assert "budget" in reason.lower()


def test_consolidate_orders_no_gap_is_already_stocked():
    path, _, _ = consolidate_orders([], [], None, delivery_confidence=0.9)
    assert path == ProcurementPath.ALREADY_STOCKED

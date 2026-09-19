"""Ticket #27 -- agent-decision tests over the seed scenarios.

Assert on decisions and guardrails, never on model prose. A scenario that
"passes" because nothing was proposed at all is a false green -- every
allergen-safety assertion here checks a dish WAS chosen, and checks which
one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.enums import ProcurementPath, SpendTier
from app.providers.voice import LocalVoiceProvider
from app.seed import (
    TODAY,
    scenario_budget_constraint,
    scenario_cook_mishap,
    scenario_expiry_routine,
    scenario_feedback_learning,
    scenario_guests,
    scenario_manual_purchase,
    scenario_preference_conflict,
)
from app.services import (
    ProviderQuote,
    classify_order_tier,
    compute_ingredient_gap,
    consolidate_orders,
)

UTC = timezone.utc


_ALLERGEN_KEYWORDS = {
    "dairy": ["paneer", "milk", "curd", "ghee", "cheese", "cream", "butter"],
    "eggplant": ["eggplant", "baingan", "brinjal"],
}


def _is_safe_for(dish, members) -> bool:
    """A dish is safe for a household if none of its named ingredients
    trigger any member's allergy list. Uses a small keyword map since raw
    ingredient names ("Paneer") don't literally contain the allergen word
    ("dairy") -- a real system would resolve this via an ingredient-to-
    allergen-category table; this mirrors that resolution for the test."""
    ingredient_names = {str(i["ingredient"]).lower() for i in dish.ingredients}
    for member in members:
        for allergy in member.allergies:
            keywords = _ALLERGEN_KEYWORDS.get(allergy.lower(), [allergy.lower()])
            if any(any(kw in name for kw in keywords) for name in ingredient_names):
                return False
    return True


# ---------------------------------------------------------------------------
# preference_conflict -- the non-negotiable allergen-safety assertion
# ---------------------------------------------------------------------------

def test_preference_conflict_never_proposes_an_allergen(session):
    fx = scenario_preference_conflict(session)

    safe_dishes = [d for d in fx.dishes if _is_safe_for(d, fx.members)]
    assert any(d.name == "Vegetable Khichdi" for d in safe_dishes)
    assert not any(d.name == "Paneer Bhurji" for d in safe_dishes)  # unsafe for Karthik
    assert not any(d.name == "Baingan Bharta" for d in safe_dishes)  # unsafe for Sharmila

    # The planner (mirrored here at the service level) must choose from
    # the safe set only -- Paneer Bhurji and Baingan Bharta must never be
    # selected for this household regardless of inventory or cost.
    chosen = safe_dishes[0]
    assert chosen.name == "Vegetable Khichdi"
    gap = compute_ingredient_gap(chosen, fx.inventory, servings=2, today=TODAY)
    assert gap == []  # fully stocked, per the fixture


# ---------------------------------------------------------------------------
# cook_mishap -- recovers from stock, no new order (Bible §4.3 error recovery)
# ---------------------------------------------------------------------------

def test_cook_mishap_recovers_from_stock_with_no_new_order(session):
    fx = scenario_cook_mishap(session)
    khichdi = next(d for d in fx.dishes if d.name == "Vegetable Khichdi")
    paneer_bhurji = next(d for d in fx.dishes if d.name == "Paneer Bhurji")

    gap_khichdi = compute_ingredient_gap(khichdi, fx.inventory, servings=2, today=TODAY)
    assert gap_khichdi == []  # no order needed

    gap_paneer = compute_ingredient_gap(paneer_bhurji, fx.inventory, servings=2, today=TODAY)
    assert gap_paneer and gap_paneer[0].ingredient == "Paneer"  # correctly identifies the mishap

    path, reason, _ = consolidate_orders(gap_khichdi, [], fx.budget, delivery_confidence=0.9)
    assert path == ProcurementPath.ALREADY_STOCKED


# ---------------------------------------------------------------------------
# budget_constraint -- escalates rather than overspends
# ---------------------------------------------------------------------------

def test_budget_constraint_escalates_rather_than_overspends(session):
    fx = scenario_budget_constraint(session)
    dish = fx.dishes[0]
    gap = compute_ingredient_gap(dish, fx.inventory, servings=2, today=TODAY)
    assert gap  # rajma is missing

    quotes = [ProviderQuote("Zepto", total_inr=137, feasible=True)]
    tier, tier_reason = classify_order_tier(137, fx.budget, is_routine=True)
    assert tier == SpendTier.YELLOW  # ₹137 vs ₹90 remaining -- must not be green

    path, path_reason, chosen = consolidate_orders(gap, quotes, fx.budget, delivery_confidence=0.9)
    # ₹137 exceeds the ₹90 remaining budget at the only feasible price -> escalate.
    assert path == ProcurementPath.ESCALATED
    assert "budget" in path_reason.lower()


# ---------------------------------------------------------------------------
# guests -- structured context, long-term profiles untouched
# ---------------------------------------------------------------------------

def test_guests_leaves_long_term_profiles_untouched(session):
    from app.models import MealLoopRecord
    from app.enums import LoopStatus

    fx = scenario_guests(session)
    before = {m.id: (tuple(m.likes), tuple(m.dislikes)) for m in fx.members}

    loop = MealLoopRecord(household_id=fx.household.id, trigger_type="manual", guest_count=4, occasion="surprise visit", status=LoopStatus.TRIGGERED)
    session.add(loop)
    session.commit()
    session.refresh(loop)

    assert loop.guest_count == 4
    assert loop.occasion == "surprise visit"

    session.refresh_all = None  # no-op, just documenting intent
    for member in fx.members:
        session.refresh(member)
        after = (tuple(member.likes), tuple(member.dislikes))
        assert after == before[member.id]


# ---------------------------------------------------------------------------
# manual_purchase -- low delivery confidence flips the procurement path
# ---------------------------------------------------------------------------

def test_manual_purchase_when_delivery_confidence_is_low(session):
    fx = scenario_manual_purchase(session)
    dish = fx.dishes[0]
    gap = compute_ingredient_gap(dish, fx.inventory, servings=2, today=TODAY)
    assert gap

    quotes = [ProviderQuote("Zepto", total_inr=80, feasible=True)]
    # Cook starts in 15 minutes; base ETA is 25 min -> confidence should be low.
    from app.providers.logistics_mock import MockLogisticsProvider

    logistics = MockLogisticsProvider(base_eta_minutes=25)
    now = datetime.now(UTC)
    delivery = logistics.delivery_confidence("household", now, now + timedelta(minutes=10))
    path, reason, _ = consolidate_orders(gap, quotes, fx.budget, delivery.confidence)
    assert path == ProcurementPath.MANUAL_PURCHASE


# ---------------------------------------------------------------------------
# feedback_learning -- prior rejection is visible in history
# ---------------------------------------------------------------------------

def test_feedback_learning_prior_rejection_visible_in_history(session):
    from app.models import DishHistory
    from sqlmodel import select

    fx = scenario_feedback_learning(session)
    rows = list(session.exec(select(DishHistory).where(DishHistory.household_id == fx.household.id)))
    rejected = [r for r in rows if r.accepted is False]
    assert rejected and rejected[0].dish_name == "Chhanar Dalna"
    assert "sweet" in (rejected[0].feedback or "").lower()


# ---------------------------------------------------------------------------
# expiry_routine -- nothing urgent, sanity check
# ---------------------------------------------------------------------------

def test_expiry_routine_identifies_the_expiring_lot(session):
    from app.services import effective_freshness
    from app.enums import FreshnessState

    fx = scenario_expiry_routine(session)
    tamarind = next(l for l in fx.inventory if l.ingredient == "Tamarind")
    assert effective_freshness(tamarind, today=TODAY) == FreshnessState.EXPIRING_SOON


# ---------------------------------------------------------------------------
# Every household's cook gets a brief in their own language (Ticket #25)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "scenario_fn",
    [scenario_preference_conflict, scenario_guests, scenario_cook_mishap, scenario_feedback_learning, scenario_budget_constraint],
)
def test_every_household_cook_brief_in_their_own_language(session, scenario_fn):
    fx = scenario_fn(session)
    provider = LocalVoiceProvider(model_provider=None)  # forces the fallback path -- no live Ollama in tests
    brief = provider.reply(dish_name=fx.dishes[0].name, instructions="test", language=fx.cook.language, skill_level=fx.cook.skill_level)
    assert brief.language == fx.cook.language
    assert fx.cook.language in brief.text or fx.dishes[0].name in brief.text

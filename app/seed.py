"""Demo fixtures -- six households, seven scenarios.

Rebuilt for the build map (the original upload's seed.py was not included
in this remediation sprint's source files, and the new enum-typed models
below are not compatible with a hand-copied version of it). These
scenarios are designed to match the exact edge cases named across the
build map's tickets and Bible §4.5's Adversarial Judge Simulation:
allergen safety, budget constraints, guest handling, cook error recovery,
and feedback-driven learning.

Each `scenario_*` function is self-contained: it creates its own
household, members, cook, dishes and inventory, and returns the created
rows so tests can assert against them directly. Ticket #27's
test_agent_decisions.py drives these through the real API/services layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session

from app.models import (
    Budget,
    CookProfile,
    Dish,
    Household,
    HouseholdMember,
    InventoryLot,
)

UTC = timezone.utc
TODAY = date(2026, 6, 1)


@dataclass
class ScenarioFixture:
    name: str
    household: Household
    members: list[HouseholdMember]
    cook: CookProfile
    dishes: list[Dish]
    inventory: list[InventoryLot]
    budget: Budget | None = None
    notes: str = ""


def _household(session: Session, name: str, language: str) -> Household:
    h = Household(name=name, default_language=language)
    session.add(h)
    session.commit()
    session.refresh(h)
    return h


def _member(session: Session, household_id: int, **kwargs) -> HouseholdMember:
    m = HouseholdMember(household_id=household_id, **kwargs)
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


def _cook(session: Session, household_id: int, **kwargs) -> CookProfile:
    c = CookProfile(household_id=household_id, **kwargs)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def _dish(session: Session, household_id: int | None, **kwargs) -> Dish:
    d = Dish(household_id=household_id, **kwargs)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _lot(session: Session, household_id: int, **kwargs) -> InventoryLot:
    kwargs.setdefault("updated_at", datetime.now(UTC))
    lot = InventoryLot(household_id=household_id, **kwargs)
    session.add(lot)
    session.commit()
    session.refresh(lot)
    return lot


def _budget(session: Session, household_id: int, **kwargs) -> Budget:
    b = Budget(household_id=household_id, **kwargs)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


# ---------------------------------------------------------------------------
# 1. expiry_routine -- an ordinary day; nothing urgent, one lot expiring soon
# ---------------------------------------------------------------------------

def scenario_expiry_routine(session: Session) -> ScenarioFixture:
    h = _household(session, "Nair Household", "Malayalam")
    members = [_member(session, h.id, name="Anjali Nair", language="Malayalam")]
    cook = _cook(session, h.id, name="Latha", language="Malayalam", skill_level="intermediate")
    dish = _dish(
        session, h.id,
        name="Sambar",
        ingredients=[{"ingredient": "Toor Dal", "quantity": 150, "unit": "g"}, {"ingredient": "Tamarind", "quantity": 20, "unit": "g"}],
        prep_minutes=30, servings=3, tags=["routine"],
    )
    inventory = [
        _lot(session, h.id, ingredient="Toor Dal", quantity=500, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=60)),
        _lot(session, h.id, ingredient="Tamarind", quantity=50, unit="g", freshness="expiring_soon", expiry_date=TODAY + timedelta(days=1)),
    ]
    budget = _budget(session, h.id, monthly_limit=3000, spent_amount=400, planned_amount=0)
    return ScenarioFixture("expiry_routine", h, members, cook, [dish], inventory, budget, "Tamarind expiring soon; nothing else urgent.")


# ---------------------------------------------------------------------------
# 2. preference_conflict -- one member's ALLERGY vs another member's WANT
# ---------------------------------------------------------------------------

def scenario_preference_conflict(session: Session) -> ScenarioFixture:
    h = _household(session, "Iyer Household", "Tamil")
    members = [
        _member(session, h.id, name="Karthik Iyer", language="Tamil", allergies=["dairy"], likes=["paneer dishes"]),
        _member(session, h.id, name="Sharmila Iyer", language="Tamil", allergies=["eggplant"], dislikes=["bitter gourd"]),
    ]
    cook = _cook(session, h.id, name="Murugan", language="Tamil", skill_level="expert", confident_dishes=["Paneer Bhurji", "Baingan Bharta"])
    dishes = [
        _dish(session, h.id, name="Paneer Bhurji", ingredients=[{"ingredient": "Paneer", "quantity": 200, "unit": "g"}], prep_minutes=20, servings=2, tags=["routine"]),
        _dish(session, h.id, name="Baingan Bharta", ingredients=[{"ingredient": "Eggplant", "quantity": 300, "unit": "g"}], prep_minutes=25, servings=2, tags=["routine"]),
        _dish(session, h.id, name="Vegetable Khichdi", ingredients=[{"ingredient": "Moong Dal", "quantity": 150, "unit": "g"}, {"ingredient": "Rice", "quantity": 100, "unit": "g"}], prep_minutes=25, servings=2, tags=["routine"]),
    ]
    inventory = [
        _lot(session, h.id, ingredient="Paneer", quantity=200, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=3)),
        _lot(session, h.id, ingredient="Eggplant", quantity=300, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=3)),
        _lot(session, h.id, ingredient="Moong Dal", quantity=500, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=60)),
        _lot(session, h.id, ingredient="Rice", quantity=1000, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=90)),
    ]
    budget = _budget(session, h.id, monthly_limit=4000, spent_amount=500, planned_amount=0)
    return ScenarioFixture(
        "preference_conflict", h, members, cook, dishes, inventory, budget,
        "Karthik is dairy-allergic (Paneer Bhurji is unsafe for him); Sharmila is eggplant-allergic "
        "(Baingan Bharta is unsafe for her). Only Vegetable Khichdi is safe for both.",
    )


# ---------------------------------------------------------------------------
# 3. guests -- a surprise dinner guest; must not rewrite long-term profiles
# ---------------------------------------------------------------------------

def scenario_guests(session: Session) -> ScenarioFixture:
    h = _household(session, "Reddy Household", "Telugu")
    members = [_member(session, h.id, name="Lakshmi Reddy", language="Telugu", dislikes=["very spicy food"])]
    cook = _cook(session, h.id, name="Ramesh", language="Telugu", skill_level="intermediate")
    dish = _dish(session, h.id, name="Pulihora", ingredients=[{"ingredient": "Rice", "quantity": 200, "unit": "g"}, {"ingredient": "Tamarind", "quantity": 30, "unit": "g"}], prep_minutes=30, servings=2, tags=["routine"])
    inventory = [
        _lot(session, h.id, ingredient="Rice", quantity=1000, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=90)),
        _lot(session, h.id, ingredient="Tamarind", quantity=100, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=30)),
    ]
    budget = _budget(session, h.id, monthly_limit=3500, spent_amount=200, planned_amount=0)
    return ScenarioFixture("guests", h, members, cook, [dish], inventory, budget, "4 unplanned guests arriving for dinner.")


# ---------------------------------------------------------------------------
# 4. cook_mishap -- paneer zeroed out, moong dal stocked; must recover
#    from stock, no new order (Bible §4.3 error recovery)
# ---------------------------------------------------------------------------

def scenario_cook_mishap(session: Session) -> ScenarioFixture:
    h = _household(session, "Iyer Household (Mishap)", "Tamil")
    members = [_member(session, h.id, name="Karthik Iyer", language="Tamil")]
    cook = _cook(session, h.id, name="Murugan", language="Tamil", skill_level="expert")
    dishes = [
        _dish(session, h.id, name="Paneer Bhurji", ingredients=[{"ingredient": "Paneer", "quantity": 200, "unit": "g"}], prep_minutes=20, servings=2, tags=["routine"]),
        _dish(session, h.id, name="Vegetable Khichdi", ingredients=[{"ingredient": "Moong Dal", "quantity": 150, "unit": "g"}], prep_minutes=25, servings=2, tags=["routine"]),
    ]
    inventory = [
        # Paneer accidentally used up / dropped -- zero quantity on record.
        _lot(session, h.id, ingredient="Paneer", quantity=0, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=3)),
        _lot(session, h.id, ingredient="Moong Dal", quantity=500, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=60)),
    ]
    budget = _budget(session, h.id, monthly_limit=3000, spent_amount=100, planned_amount=0)
    return ScenarioFixture(
        "cook_mishap", h, members, cook, dishes, inventory, budget,
        "Paneer quantity was zeroed after a mishap; Vegetable Khichdi should be recoverable "
        "entirely from stocked moong dal with no new order.",
    )


# ---------------------------------------------------------------------------
# 5. feedback_learning -- prior DishHistory rejections should inform planning
# ---------------------------------------------------------------------------

def scenario_feedback_learning(session: Session) -> ScenarioFixture:
    from app.models import DishHistory

    h = _household(session, "Banerjee Household", "Bengali")
    members = [_member(session, h.id, name="Riya Banerjee", language="Bengali", dietary_preferences=["pescatarian"])]
    cook = _cook(session, h.id, name="Debjani", language="Bengali", skill_level="intermediate")
    dishes = [
        _dish(session, h.id, name="Shorshe Ilish", ingredients=[{"ingredient": "Hilsa Fish", "quantity": 400, "unit": "g"}], prep_minutes=35, servings=3, tags=["routine"]),
        _dish(session, h.id, name="Chhanar Dalna", ingredients=[{"ingredient": "Paneer", "quantity": 250, "unit": "g"}], prep_minutes=30, servings=3, tags=["routine"]),
    ]
    inventory = [
        _lot(session, h.id, ingredient="Hilsa Fish", quantity=400, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=2)),
        _lot(session, h.id, ingredient="Paneer", quantity=250, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=3)),
    ]
    session.add(DishHistory(household_id=h.id, dish_name="Chhanar Dalna", served_on=TODAY - timedelta(days=3), accepted=False, rating=2, feedback="too sweet, please reduce sugar"))
    session.commit()
    budget = _budget(session, h.id, monthly_limit=4500, spent_amount=600, planned_amount=0)
    return ScenarioFixture("feedback_learning", h, members, cook, dishes, inventory, budget, "Chhanar Dalna was rejected last time (too sweet).")


# ---------------------------------------------------------------------------
# 6. budget_constraint -- the ₹137-vs-₹90-remaining case (Ticket #11)
# ---------------------------------------------------------------------------

def scenario_budget_constraint(session: Session) -> ScenarioFixture:
    h = _household(session, "Kapoor Household", "Hindi")
    members = [_member(session, h.id, name="Rohan Kapoor", language="Hindi")]
    cook = _cook(session, h.id, name="Sunita", language="Hindi", skill_level="beginner")
    dish = _dish(session, h.id, name="Rajma Chawal", ingredients=[{"ingredient": "Rajma", "quantity": 200, "unit": "g"}, {"ingredient": "Rice", "quantity": 200, "unit": "g"}], prep_minutes=40, servings=2, tags=["routine"])
    inventory = [
        _lot(session, h.id, ingredient="Rice", quantity=1000, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=90)),
    ]
    # 227 limit, 137 already spent -> ₹90 remaining. A ₹137 order should
    # classify as yellow, not green (Ticket #11's explicit seed case).
    budget = _budget(session, h.id, monthly_limit=227, spent_amount=137, planned_amount=0)
    return ScenarioFixture("budget_constraint", h, members, cook, [dish], inventory, budget, "₹90 remaining this month; Rajma must be bought.")


# ---------------------------------------------------------------------------
# 7. manual_purchase -- a delivery window too tight to trust (Ticket #19/#21)
# ---------------------------------------------------------------------------

def scenario_manual_purchase(session: Session) -> ScenarioFixture:
    h = _household(session, "Sharma Household", "Hindi")
    members = [_member(session, h.id, name="Priya Sharma", language="Hindi")]
    cook = _cook(session, h.id, name="Priya Sharma", language="Hindi", skill_level="intermediate", available_hours=["18:00-19:00"])
    dish = _dish(session, h.id, name="Aloo Gobi", ingredients=[{"ingredient": "Potato", "quantity": 300, "unit": "g"}, {"ingredient": "Cauliflower", "quantity": 300, "unit": "g"}], prep_minutes=25, servings=2, tags=["routine"])
    inventory = [
        _lot(session, h.id, ingredient="Potato", quantity=100, unit="g", freshness="fresh", expiry_date=TODAY + timedelta(days=20)),
    ]
    budget = _budget(session, h.id, monthly_limit=3000, spent_amount=300, planned_amount=0)
    return ScenarioFixture("manual_purchase", h, members, cook, [dish], inventory, budget, "Cook starts in 15 minutes; delivery confidence should be too low to trust.")


SCENARIOS = {
    "expiry_routine": scenario_expiry_routine,
    "preference_conflict": scenario_preference_conflict,
    "guests": scenario_guests,
    "cook_mishap": scenario_cook_mishap,
    "feedback_learning": scenario_feedback_learning,
    "budget_constraint": scenario_budget_constraint,
    "manual_purchase": scenario_manual_purchase,
}


def seed_all(session: Session) -> dict[str, ScenarioFixture]:
    return {name: fn(session) for name, fn in SCENARIOS.items()}

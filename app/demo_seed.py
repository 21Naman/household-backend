"""The three demo households the deployed instance is populated with.

`app/seed.py` already exists and is used by tests/test_agent_decisions.py, but
it is not this. It pins `TODAY = date(2026, 6, 1)` so its fixtures stay
deterministic for assertions, which means running it against a live database
today writes inventory that expired months ago -- and `effective_freshness`
would correctly mark all of it unusable. It also creates no preference
signals, no leftovers and no dish history.

This module is for a database you actually demo against. Every date is
relative to the moment it runs, and it fills in every table the planner reads:

    household -> members -> cook profile -> budget -> dishes
              -> inventory -> preference signals -> leftovers -> history

It lives under `app/` rather than in `scripts/` because `app/main.py` seeds
and refreshes the demo data on a deployed instance, and `scripts/` is not an
importable package. `scripts/seed_demo_households.py` is now a thin CLI over
this module.

Two details that are easy to get wrong and silently ruin the data:

  * InventoryLot.updated_at must be recent. Anything older than
    `inventory_recency_window_hours` (48h) reads as STALE no matter how
    distant its expiry date, so it stops counting toward stock and recipe
    generation starts failing the 60% availability check for no visible
    reason. On create, the default factory handles it; on refresh,
    `refresh_or_create` pushes it forward explicitly -- which is why that
    function sets a field this docstring otherwise tells you never to touch.
  * Transient preferences belong in PreferenceSignal with `expires_on`, never
    in HouseholdMember.likes/dislikes. That split is the whole point of
    Ticket #23's short-term vs long-term memory boundary.

**The spec dicts below are read-only.** Nothing in this module may mutate
them: `refresh_or_create` is called repeatedly inside one long-lived process
by the reseed job, so a `.pop()` off a spec would work once and then raise
`KeyError` on every subsequent run. Read optional keys with `.get()` and
build the remaining kwargs by comprehension.

Two entry points:

  * `seed(session, spec)` -- create a household that does not exist yet.
  * `refresh_or_create(session, spec)` -- restore a household to its seeded
    shape without deleting a single row, so ids stay stable for a judge who
    is mid-session.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.models import (
    Budget,
    CookProfile,
    Dish,
    DishHistory,
    Household,
    HouseholdMember,
    InventoryLot,
    Leftover,
    PreferenceSignal,
)


def days(n: int, today: date | None = None) -> date:
    """`n` days from `today`, defaulting to the actual current date.

    Deliberately not anchored to a module-level `TODAY = date.today()`
    evaluated at import: a deployed process reseeds every few hours and may
    live for days, so an import-time anchor would keep writing dates relative
    to the boot date and the demo data would drift back out of the freshness
    window it exists to stay inside.
    """
    return (today or date.today()) + timedelta(days=n)


# --------------------------------------------------------------------------
# Three households with genuinely different shapes, so each exercises a
# different branch of the planner rather than being the same demo three times.
# --------------------------------------------------------------------------

HOUSEHOLDS = [
    {
        # Well-stocked, comfortable budget, Hinglish cook. The happy path:
        # recipe generates, almost everything is in the kitchen, audio
        # briefing comes back in Hinglish.
        "household": {"name": "Sharma Household", "default_language": "Hindi"},
        "members": [
            {
                "name": "Anjali Sharma", "language": "Hindi",
                "dietary_preferences": ["vegetarian"],
                "allergies": ["peanuts"],
                "health_constraints": ["lower sodium"],
                "likes": ["paneer", "rajma", "North Indian food"],
                "dislikes": ["karela", "excess oil"],
            },
            {
                "name": "Rohit Sharma", "language": "Hindi",
                "dietary_preferences": ["vegetarian"],
                "allergies": [],
                "health_constraints": [],
                "likes": ["chole", "spicy food"],
                "dislikes": ["lauki"],
            },
            {
                "name": "Dadi", "language": "Hindi",
                "dietary_preferences": ["vegetarian", "no onion no garlic"],
                "allergies": [],
                "health_constraints": ["diabetic", "soft food only"],
                "likes": ["khichdi", "dal"],
                "dislikes": ["fried food"],
            },
        ],
        "cook": {
            "name": "Asha", "language": "Hindi", "skill_level": "intermediate",
            "available_hours": ["07:00-09:30", "17:30-20:30"],
            "confident_dishes": [
                "Rajma Chawal", "Paneer Bhurji", "Aloo Gobhi",
                "Moong Dal Khichdi", "Jeera Rice", "Chole",
            ],
        },
        "budget": {"monthly_limit": 9000, "spent_amount": 3100, "planned_amount": 400},
        "inventory": [
            ("Rice", 2200, "g", 40, "fresh", "pantry"),
            ("Moong Dal", 900, "g", 60, "fresh", "pantry"),
            ("Rajma", 700, "g", 90, "fresh", "pantry"),
            ("Paneer", 250, "g", 3, "fresh", "fridge"),
            ("Tomato", 6, "count", 4, "fresh", "fridge"),
            ("Onion", 1500, "g", 20, "fresh", "pantry"),
            ("Potato", 1200, "g", 18, "fresh", "pantry"),
            ("Milk", 1000, "ml", 2, "fresh", "fridge"),
            ("Spinach", 300, "g", 1, "use_immediately", "fridge"),
            ("Curd", 400, "g", 5, "fresh", "fridge"),
        ],
        "preferences": [
            {"signal": "avoid heavy fried food this week, Dadi's check-up is Friday",
             "sentiment": "negative", "context": "health", "confidence": 0.9,
             "expires_in_days": 7},
            {"signal": "Rohit asked for something spicy at least twice this week",
             "sentiment": "positive", "context": "request", "confidence": 0.7,
             "expires_in_days": 5},
            # Deliberately already expired. Every household carries one so a
            # judge can see the expiry rule actually fire: this is excluded
            # from the model's context by the same predicate the planner uses,
            # and the UI renders it struck through. Without it the rule is
            # correct but invisible, because the reseed job keeps pushing the
            # live signals' dates forward.
            {"signal": "Rohit wanted pav bhaji for last Sunday's lunch",
             "sentiment": "positive", "context": "request", "confidence": 0.8,
             "expires_in_days": -3},
        ],
        "leftovers": [
            {"dish_name": "Rajma", "portions": 2, "stored_days_ago": 1, "expiry_in_days": 2,
             "reuse_suggestions": ["rajma rice bowl", "rajma paratha filling"]},
        ],
        "dishes": [
            {"name": "Rajma Chawal", "prep_minutes": 45, "servings": 4,
             "ingredients": [{"ingredient": "Rajma", "quantity": 250, "unit": "g"},
                             {"ingredient": "Rice", "quantity": 400, "unit": "g"},
                             {"ingredient": "Onion", "quantity": 200, "unit": "g"},
                             {"ingredient": "Tomato", "quantity": 3, "unit": "count"}],
             "nutrition_notes": ["Rajma provides plant protein and fibre."],
             "tags": ["north-indian", "comfort"], "cook_skill_required": "intermediate"},
            {"name": "Moong Dal Khichdi", "prep_minutes": 30, "servings": 4,
             "ingredients": [{"ingredient": "Moong Dal", "quantity": 200, "unit": "g"},
                             {"ingredient": "Rice", "quantity": 200, "unit": "g"}],
             "nutrition_notes": ["Soft, low-oil, suitable for a diabetic diet."],
             "tags": ["light", "soft"], "cook_skill_required": "beginner"},
        ],
        "history": [
            {"dish_name": "Paneer Bhurji", "served_days_ago": 2, "accepted": True, "rating": 4,
             "feedback": "Dadi found it slightly oily", "leftovers_portions": 0},
            {"dish_name": "Aloo Gobhi", "served_days_ago": 5, "accepted": True, "rating": 5,
             "feedback": "everyone finished it", "leftovers_portions": 0},
            {"dish_name": "Karela Sabzi", "served_days_ago": 9, "accepted": False, "rating": 2,
             "feedback": "nobody ate it, Anjali dislikes karela", "leftovers_portions": 3,
             "cook_modifications": "added extra jaggery to cut bitterness"},
        ],
    },
    {
        # Tight budget, partially stocked, English-speaking cook. Exercises
        # the missing-ingredient gap, quoting and the spend-tier path, and
        # gets an en-IN voice rather than Hinglish.
        "household": {"name": "Iyer Household", "default_language": "Tamil"},
        "members": [
            {
                "name": "Lakshmi Iyer", "language": "Tamil",
                "dietary_preferences": ["vegetarian"],
                "allergies": [],
                "health_constraints": ["lactose intolerant"],
                "likes": ["sambar", "rasam", "South Indian food"],
                "dislikes": ["capsicum"],
            },
            {
                "name": "Karthik Iyer", "language": "English",
                "dietary_preferences": ["eggetarian"],
                "allergies": ["shellfish"],
                "health_constraints": [],
                "likes": ["curd rice", "filter coffee"],
                "dislikes": ["bitter gourd"],
            },
        ],
        "cook": {
            "name": "Murugan", "language": "English", "skill_level": "expert",
            "available_hours": ["06:30-09:00", "18:00-21:00"],
            "confident_dishes": [
                "Sambar", "Rasam", "Lemon Rice", "Curd Rice",
                "Vegetable Poriyal", "Upma",
            ],
        },
        "budget": {"monthly_limit": 4500, "spent_amount": 3900, "planned_amount": 250},
        "inventory": [
            ("Rice", 1500, "g", 45, "fresh", "pantry"),
            ("Toor Dal", 300, "g", 70, "fresh", "pantry"),
            ("Tamarind", 150, "g", 120, "fresh", "pantry"),
            ("Mustard Seeds", 100, "g", 200, "fresh", "pantry"),
            ("Curry Leaves", 30, "g", 2, "expiring_soon", "fridge"),
            ("Carrot", 400, "g", 6, "fresh", "fridge"),
            ("Coconut", 1, "count", 3, "fresh", "pantry"),
        ],
        "preferences": [
            # Never expires, and deliberately restates Lakshmi's
            # health_constraints. The member field is the FACT the planner may
            # not violate; the signal is the NARRATIVE with provenance --
            # sentiment, confidence, context, who said it, when. They have
            # different lifecycles and different write paths, and a constraint
            # plus the reason for it is strictly more useful to a planner than
            # the constraint alone. This is not duplication to remove.
            {"signal": "no dairy in the evening meal, Lakshmi is lactose intolerant",
             "sentiment": "negative", "context": "health", "confidence": 1.0,
             "expires_in_days": None},
            {"signal": "keep spend low until the month turns over",
             "sentiment": "neutral", "context": "budget", "confidence": 0.95,
             "expires_in_days": 10},
            {"signal": "Karthik's colleagues came for dinner last weekend, make extra rasam",
             "sentiment": "neutral", "context": "occasion", "confidence": 1.0,
             "expires_in_days": -3},
        ],
        "leftovers": [
            {"dish_name": "Sambar", "portions": 3, "stored_days_ago": 0, "expiry_in_days": 2,
             "reuse_suggestions": ["sambar rice", "base for vegetable stew"]},
            {"dish_name": "Lemon Rice", "portions": 1, "stored_days_ago": 2, "expiry_in_days": 1,
             "reuse_suggestions": ["lunchbox portion"]},
        ],
        "dishes": [
            {"name": "Sambar", "prep_minutes": 40, "servings": 4,
             "ingredients": [{"ingredient": "Toor Dal", "quantity": 200, "unit": "g"},
                             {"ingredient": "Tamarind", "quantity": 30, "unit": "g"},
                             {"ingredient": "Carrot", "quantity": 200, "unit": "g"}],
             "nutrition_notes": ["Lentil-based, dairy free."],
             "tags": ["south-indian", "dairy-free"], "cook_skill_required": "intermediate"},
        ],
        "history": [
            {"dish_name": "Curd Rice", "served_days_ago": 3, "accepted": False, "rating": 2,
             "feedback": "Lakshmi could not eat it - dairy", "leftovers_portions": 2,
             "cook_modifications": "made a separate lemon rice portion"},
            {"dish_name": "Rasam", "served_days_ago": 6, "accepted": True, "rating": 5,
             "feedback": "asked for it again", "leftovers_portions": 0},
        ],
    },
    {
        # Beginner cook, guests expected, and inventory on the edge of
        # expiring. Exercises the expiry/leftover-first logic and the
        # beginner register in the spoken briefing.
        "household": {"name": "Khan Household", "default_language": "Hinglish"},
        "members": [
            {
                "name": "Sana Khan", "language": "Hinglish",
                "dietary_preferences": ["halal"],
                "allergies": ["tree nuts"],
                "health_constraints": [],
                "likes": ["biryani", "kebabs"],
                "dislikes": ["mushroom"],
            },
            {
                "name": "Imran Khan", "language": "Hinglish",
                "dietary_preferences": ["halal"],
                "allergies": [],
                "health_constraints": ["high blood pressure"],
                "likes": ["grilled food"],
                "dislikes": ["very salty food"],
            },
        ],
        "cook": {
            "name": "Rehana", "language": "Hinglish", "skill_level": "beginner",
            "available_hours": ["08:00-10:00", "18:30-21:00"],
            "confident_dishes": ["Egg Curry", "Jeera Rice", "Mixed Vegetable Pulao"],
        },
        "budget": {"monthly_limit": 7000, "spent_amount": 2200, "planned_amount": 900},
        "inventory": [
            ("Rice", 1800, "g", 50, "fresh", "pantry"),
            ("Eggs", 12, "count", 9, "fresh", "fridge"),
            ("Onion", 900, "g", 14, "fresh", "pantry"),
            ("Tomato", 4, "count", 1, "expiring_soon", "fridge"),
            ("Yogurt", 500, "g", 0, "use_immediately", "fridge"),
            ("Green Peas", 400, "g", 30, "fresh", "freezer"),
            ("Ginger Garlic Paste", 200, "g", 25, "fresh", "fridge"),
            ("Coriander", 100, "g", 1, "expiring_soon", "fridge"),
        ],
        "preferences": [
            {"signal": "four guests coming Saturday evening",
             "sentiment": "neutral", "context": "occasion", "confidence": 1.0,
             "expires_in_days": 4},
            # Same deliberate restatement as Iyer's: the fact lives on
            # Imran's health_constraints, the narrative lives here.
            {"signal": "Imran is cutting down on salt on doctor's advice",
             "sentiment": "negative", "context": "health", "confidence": 1.0,
             "expires_in_days": None},
            # Never expires: how skilled the cook is is a standing property of
            # a person, not an event with a date. It previously carried a
            # 30-day expiry, which would have quietly stopped telling the
            # model that Rehana needs step-by-step dishes.
            {"signal": "Rehana is new to biryani, prefers step-by-step dishes",
             "sentiment": "neutral", "context": "cook skill", "confidence": 0.8,
             "expires_in_days": None},
            {"signal": "Sana had exams last week, keep dinners quick",
             "sentiment": "neutral", "context": "occasion", "confidence": 0.9,
             "expires_in_days": -3},
        ],
        "leftovers": [
            {"dish_name": "Jeera Rice", "portions": 2, "stored_days_ago": 1, "expiry_in_days": 1,
             "reuse_suggestions": ["fried rice base", "serve with egg curry"]},
        ],
        "dishes": [
            {"name": "Egg Curry", "prep_minutes": 35, "servings": 4,
             "ingredients": [{"ingredient": "Eggs", "quantity": 6, "unit": "count"},
                             {"ingredient": "Onion", "quantity": 200, "unit": "g"},
                             {"ingredient": "Tomato", "quantity": 3, "unit": "count"}],
             "nutrition_notes": ["Protein from eggs; salt kept low."],
             "tags": ["everyday"], "cook_skill_required": "beginner"},
        ],
        "history": [
            {"dish_name": "Mixed Vegetable Pulao", "served_days_ago": 1, "accepted": True,
             "rating": 4, "feedback": "good but slightly undercooked rice",
             "leftovers_portions": 2, "cook_modifications": "used less water than the recipe said"},
            {"dish_name": "Egg Curry", "served_days_ago": 4, "accepted": True, "rating": 5,
             "feedback": "Rehana's best so far", "leftovers_portions": 0},
        ],
    },
]

def _preference_fields(preference: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Split a preference spec into (expires_in_days, model kwargs).

    Built by comprehension rather than `.pop()`: the spec dicts are module
    level and the reseed job calls this repeatedly inside one process, so
    popping would work on the first pass and raise KeyError on every one
    after it.
    """
    return (
        preference.get("expires_in_days"),
        {key: value for key, value in preference.items() if key != "expires_in_days"},
    )


def _history_fields(entry: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Same non-destructive split for a dish-history spec."""
    return (
        entry.get("served_days_ago", 0),
        {key: value for key, value in entry.items() if key != "served_days_ago"},
    )


def seed(session: Session, spec: dict) -> tuple[str, bool]:
    """Create a demo household that does not exist yet.

    Idempotent by household name: returns (name, False) untouched if it is
    already there. Use `refresh_or_create` when you want an existing
    household restored rather than skipped.
    """
    name = spec["household"]["name"]
    existing = session.exec(select(Household).where(Household.name == name)).first()
    if existing is not None:
        return name, False

    today = date.today()

    household = Household(**spec["household"])
    session.add(household)
    session.commit()
    session.refresh(household)
    hid = household.id

    members: list[HouseholdMember] = []
    for member in spec["members"]:
        row = HouseholdMember(household_id=hid, **member)
        session.add(row)
        members.append(row)

    session.add(CookProfile(household_id=hid, **spec["cook"]))
    session.add(Budget(household_id=hid, **spec["budget"]))

    for ingredient, quantity, unit, expires_in, freshness, location in spec["inventory"]:
        session.add(
            InventoryLot(
                household_id=hid,
                ingredient=ingredient,
                quantity=quantity,
                unit=unit,
                purchased_on=days(-3, today),
                expiry_date=days(expires_in, today),
                freshness=freshness,
                storage_location=location,
                confirmed=True,
                # updated_at is left to its default_factory on purpose: a
                # backdated value beyond the 48h recency window would make
                # this lot read as STALE and drop out of stock entirely.
            )
        )

    session.commit()
    for member in members:
        session.refresh(member)

    for preference in spec["preferences"]:
        expires_in, fields = _preference_fields(preference)
        session.add(
            PreferenceSignal(
                household_id=hid,
                member_id=members[0].id if members else None,
                expires_on=days(expires_in, today) if expires_in is not None else None,
                **fields,
            )
        )

    for leftover in spec["leftovers"]:
        session.add(
            Leftover(
                household_id=hid,
                dish_name=leftover["dish_name"],
                portions=leftover["portions"],
                stored_on=days(-leftover["stored_days_ago"], today),
                expiry_date=days(leftover["expiry_in_days"], today),
                reuse_suggestions=leftover["reuse_suggestions"],
            )
        )

    for dish in spec["dishes"]:
        session.add(Dish(household_id=hid, **dish))

    for entry in spec["history"]:
        served_days_ago, fields = _history_fields(entry)
        session.add(DishHistory(household_id=hid, served_on=days(-served_days_ago, today), **fields))

    session.commit()
    return name, True


def _key(value: str) -> str:
    return value.strip().casefold()


def _index(rows: list[Any], attribute: str) -> dict[str, Any]:
    """Index existing rows by a natural key, first occurrence winning."""
    indexed: dict[str, Any] = {}
    for row in rows:
        indexed.setdefault(_key(getattr(row, attribute)), row)
    return indexed


def refresh_or_create(session: Session, spec: dict) -> tuple[str, str]:
    """Bring a demo household back to its seeded shape without deleting a row.

    `seed` is idempotent-by-name and returns early when the household already
    exists, which means re-running it refreshes nothing: quantities stay
    drained from whatever anyone cooked, expiry dates keep sliding into the
    past, and `InventoryLot.updated_at` ages past the 48h recency window until
    `effective_freshness` marks the whole kitchen STALE, stock stops counting,
    and recipe generation starts failing the availability check for no visible
    reason. This function is what actually keeps a long-lived demo alive.

    Nothing here deletes anything. Rows are matched on a natural key and
    updated in place, so household, member, loop, approval and audit ids all
    survive and a judge who is mid-session is never broken. Rows a judge added
    themselves are left alone: this restores the seeded rows, it does not
    assert ownership of the household.

    Deliberately NOT refreshed: MealLoopRecord, ApprovalRequest, AuditEvent
    and LocalTask. Those are the record of what happened, and a demo-data job
    has no business rewriting history.

    Returns (name, "created" | "refreshed").
    """
    name = spec["household"]["name"]
    household = session.exec(select(Household).where(Household.name == name)).first()
    if household is None:
        seeded_name, _ = seed(session, spec)
        return seeded_name, "created"

    today = date.today()
    hid = household.id
    household.default_language = spec["household"]["default_language"]
    session.add(household)

    def rows_for(model) -> list[Any]:
        return list(session.exec(select(model).where(model.household_id == hid)))

    # -- members ---------------------------------------------------------
    existing_members = _index(rows_for(HouseholdMember), "name")
    for member in spec["members"]:
        row = existing_members.get(_key(member["name"]))
        if row is None:
            row = HouseholdMember(household_id=hid, **member)
        else:
            for field, value in member.items():
                setattr(row, field, value)
        session.add(row)

    # -- cook profile and budget (one row each, enforced by a unique key) --
    cook = session.exec(select(CookProfile).where(CookProfile.household_id == hid)).first()
    if cook is None:
        cook = CookProfile(household_id=hid, **spec["cook"])
    else:
        for field, value in spec["cook"].items():
            setattr(cook, field, value)
    session.add(cook)

    budget = session.exec(select(Budget).where(Budget.household_id == hid)).first()
    if budget is None:
        budget = Budget(household_id=hid, **spec["budget"])
    else:
        for field, value in spec["budget"].items():
            setattr(budget, field, value)
    session.add(budget)

    # -- inventory -------------------------------------------------------
    existing_lots = _index(rows_for(InventoryLot), "ingredient")
    for ingredient, quantity, unit, expires_in, freshness, location in spec["inventory"]:
        lot = existing_lots.get(_key(ingredient))
        if lot is None:
            lot = InventoryLot(household_id=hid, ingredient=ingredient, confirmed=True)
        lot.quantity = quantity
        lot.unit = unit
        lot.purchased_on = days(-3, today)
        lot.expiry_date = days(expires_in, today)
        lot.freshness = freshness
        lot.storage_location = location
        # The one field the seed path deliberately never sets, and the one
        # this path must. A lot left with its original updated_at ages out of
        # inventory_recency_window_hours and reads STALE no matter how distant
        # its expiry date, which is the exact failure this job exists to
        # prevent. Pushing it forward is honest: the row really was just
        # rewritten.
        lot.updated_at = datetime.now(UTC)
        session.add(lot)

    # -- preference signals ----------------------------------------------
    existing_signals = _index(rows_for(PreferenceSignal), "signal")
    members = rows_for(HouseholdMember)
    for preference in spec["preferences"]:
        expires_in, fields = _preference_fields(preference)
        signal = existing_signals.get(_key(preference["signal"]))
        if signal is None:
            signal = PreferenceSignal(
                household_id=hid,
                member_id=members[0].id if members else None,
                **fields,
            )
        else:
            for field, value in fields.items():
                setattr(signal, field, value)
        # Re-anchored every run, so "expires in 4 days" and "expired 3 days
        # ago" both stay true relative to now instead of drifting.
        signal.expires_on = days(expires_in, today) if expires_in is not None else None
        session.add(signal)

    # -- leftovers -------------------------------------------------------
    existing_leftovers = _index(rows_for(Leftover), "dish_name")
    for leftover in spec["leftovers"]:
        row = existing_leftovers.get(_key(leftover["dish_name"]))
        if row is None:
            row = Leftover(household_id=hid, dish_name=leftover["dish_name"])
        row.portions = leftover["portions"]
        row.stored_on = days(-leftover["stored_days_ago"], today)
        row.expiry_date = days(leftover["expiry_in_days"], today)
        row.reuse_suggestions = leftover["reuse_suggestions"]
        session.add(row)

    # -- dishes ----------------------------------------------------------
    existing_dishes = _index(rows_for(Dish), "name")
    for dish in spec["dishes"]:
        row = existing_dishes.get(_key(dish["name"]))
        if row is None:
            row = Dish(household_id=hid, **dish)
        else:
            for field, value in dish.items():
                setattr(row, field, value)
        session.add(row)

    # -- dish history ----------------------------------------------------
    existing_history = _index(rows_for(DishHistory), "dish_name")
    for entry in spec["history"]:
        served_days_ago, fields = _history_fields(entry)
        row = existing_history.get(_key(entry["dish_name"]))
        if row is None:
            row = DishHistory(household_id=hid, **fields)
        else:
            for field, value in fields.items():
                setattr(row, field, value)
        row.served_on = days(-served_days_ago, today)
        session.add(row)

    session.commit()
    return name, "refreshed"


def run_demo_seed(db_engine=None) -> list[tuple[str, str]]:
    """The scheduler's callback: takes no required arguments and owns its own
    session, matching app/core/unclosed_sweep.py::run_unclosed_sweep.

    The engine is resolved lazily through the module rather than bound at
    import (`from app.database import engine`) because tests/conftest.py
    reassigns `app.database.engine` per test; an import-time binding would
    hold the old object and write to the wrong database.
    """
    import app.database as database_module

    active_engine = db_engine or database_module.engine
    with Session(active_engine) as session:
        return [refresh_or_create(session, spec) for spec in HOUSEHOLDS]

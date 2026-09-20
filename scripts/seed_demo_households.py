"""Populate the database with three fully-furnished demo households.

app/seed.py already exists and is used by tests/test_agent_decisions.py, but
it is not this. It pins `TODAY = date(2026, 6, 1)` so its fixtures stay
deterministic for assertions, which means running it against a live database
today writes inventory that expired months ago -- and `effective_freshness`
would correctly mark all of it unusable. It also creates no preference
signals, no leftovers and no dish history.

This script is for a database you actually demo against. Every date is
relative to the day it runs, and it fills in every table the planner reads:

    household -> members -> cook profile -> budget -> dishes
              -> inventory -> preference signals -> leftovers -> history

Two details that are easy to get wrong and silently ruin the data:

  * InventoryLot.updated_at must be recent. Anything older than
    `inventory_recency_window_hours` (48h) reads as STALE no matter how
    distant its expiry date, so it stops counting toward stock and recipe
    generation starts failing the 60% availability check for no visible
    reason. The default factory handles this; do not backdate it.
  * Transient preferences belong in PreferenceSignal with `expires_on`, never
    in HouseholdMember.likes/dislikes. That split is the whole point of
    Ticket #23's short-term vs long-term memory boundary.

Idempotent by household name: re-running skips households that already exist
rather than duplicating them. It never deletes or modifies anything.

    python scripts/seed_demo_households.py
    python scripts/seed_demo_households.py --list
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select

from app.database import engine, initialize_database
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

TODAY = date.today()


def days(n: int) -> date:
    return TODAY + timedelta(days=n)


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
            {"signal": "no dairy in the evening meal, Lakshmi is lactose intolerant",
             "sentiment": "negative", "context": "health", "confidence": 1.0,
             "expires_in_days": None},
            {"signal": "keep spend low until the month turns over",
             "sentiment": "neutral", "context": "budget", "confidence": 0.95,
             "expires_in_days": 10},
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
            {"signal": "Imran is cutting down on salt on doctor's advice",
             "sentiment": "negative", "context": "health", "confidence": 1.0,
             "expires_in_days": None},
            {"signal": "Rehana is new to biryani, prefers step-by-step dishes",
             "sentiment": "neutral", "context": "cook skill", "confidence": 0.8,
             "expires_in_days": 30},
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


def seed(session: Session, spec: dict) -> tuple[str, bool]:
    name = spec["household"]["name"]
    existing = session.exec(select(Household).where(Household.name == name)).first()
    if existing is not None:
        return name, False

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
                purchased_on=days(-3),
                expiry_date=days(expires_in),
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
        expires_in = preference.pop("expires_in_days")
        session.add(
            PreferenceSignal(
                household_id=hid,
                member_id=members[0].id if members else None,
                expires_on=days(expires_in) if expires_in is not None else None,
                **preference,
            )
        )

    for leftover in spec["leftovers"]:
        session.add(
            Leftover(
                household_id=hid,
                dish_name=leftover["dish_name"],
                portions=leftover["portions"],
                stored_on=days(-leftover["stored_days_ago"]),
                expiry_date=days(leftover["expiry_in_days"]),
                reuse_suggestions=leftover["reuse_suggestions"],
            )
        )

    for dish in spec["dishes"]:
        session.add(Dish(household_id=hid, **dish))

    for entry in spec["history"]:
        served_days_ago = entry.pop("served_days_ago")
        session.add(DishHistory(household_id=hid, served_on=days(-served_days_ago), **entry))

    session.commit()
    return name, True


def summarise(session: Session) -> None:
    print(f"\n{'household':<22} {'members':>7} {'inv':>4} {'prefs':>6} {'left':>5} {'hist':>5} {'dishes':>7}  cook")
    print("-" * 92)
    for household in session.exec(select(Household)).all():
        hid = household.id

        def count(model) -> int:
            return len(session.exec(select(model).where(model.household_id == hid)).all())

        cook = session.exec(select(CookProfile).where(CookProfile.household_id == hid)).first()
        cook_label = f"{cook.name} ({cook.language}, {cook.skill_level})" if cook else "-"
        print(
            f"#{hid} {household.name:<18} {count(HouseholdMember):>7} {count(InventoryLot):>4} "
            f"{count(PreferenceSignal):>6} {count(Leftover):>5} {count(DishHistory):>5} "
            f"{count(Dish):>7}  {cook_label}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="show what is in the database and exit")
    args = parser.parse_args()

    initialize_database()
    with Session(engine) as session:
        if args.list:
            summarise(session)
            return 0
        for spec in HOUSEHOLDS:
            name, created = seed(session, spec)
            print(f"{'created' if created else 'exists, skipped'}: {name}")
        summarise(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

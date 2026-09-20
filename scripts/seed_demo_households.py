"""CLI over app/demo_seed.py — populate or refresh the three demo households.

The data and the seeding logic live in `app/demo_seed.py`, not here, because
a deployed instance seeds and refreshes on startup and on a schedule, and
`scripts/` is not an importable package. This file is the command-line face
of that module and nothing else.

    python scripts/seed_demo_households.py            # create anything missing
    python scripts/seed_demo_households.py --refresh  # restore existing rows too
    python scripts/seed_demo_households.py --list     # show what is there
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select

from app.database import engine, initialize_database
from app.demo_seed import HOUSEHOLDS, refresh_or_create, seed
from app.models import (
    CookProfile,
    Dish,
    DishHistory,
    Household,
    HouseholdMember,
    InventoryLot,
    Leftover,
    PreferenceSignal,
)


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
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="restore existing households to their seeded shape instead of skipping them",
    )
    args = parser.parse_args()

    initialize_database()
    with Session(engine) as session:
        if args.list:
            summarise(session)
            return 0
        for spec in HOUSEHOLDS:
            if args.refresh:
                name, action = refresh_or_create(session, spec)
            else:
                name, created = seed(session, spec)
                action = "created" if created else "exists, skipped"
            print(f"{action}: {name}")
        summarise(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Ticket #16 — LocalStateStore.

Wraps the existing SQLModel repositories behind the StateStore protocol
from app.core.interfaces, without changing the schema. This is the
contract Ticket #32's DynamoDBStateStore must satisfy identically -- the
key design below is deliberately single-table-friendly (everything keyed
by household_id) so that swap is real, not aspirational.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

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
from app.repositories import Repository

UTC = timezone.utc


@dataclass
class HouseholdState:
    household_id: int
    household: Household | None
    members: list[HouseholdMember] = field(default_factory=list)
    cook_profile: CookProfile | None = None
    inventory: list[InventoryLot] = field(default_factory=list)
    leftovers: list[Leftover] = field(default_factory=list)
    dish_history: list[DishHistory] = field(default_factory=list)
    budget: Budget | None = None
    preference_signals: list[PreferenceSignal] = field(default_factory=list)
    last_updated: datetime = field(default_factory=lambda: datetime.now(UTC))


class LocalStateStore:
    """BUILD IT implementation. The planner must only ever import this
    class (or, in SHIP IT, DynamoDBStateStore) through app.core.interfaces
    -- never app.repositories directly."""

    def __init__(self, session: Session):
        self.session = session

    def get_household_state(self, household_id: int) -> HouseholdState:
        households = Repository(Household, self.session)
        members = Repository(HouseholdMember, self.session)
        cooks = Repository(CookProfile, self.session)
        inventory = Repository(InventoryLot, self.session)
        leftovers = Repository(Leftover, self.session)
        history = Repository(DishHistory, self.session)
        budgets = Repository(Budget, self.session)
        signals = Repository(PreferenceSignal, self.session)

        household = self.session.get(Household, household_id)
        inventory_rows = inventory.list_for_household(household_id)
        cook_rows = cooks.list_for_household(household_id)
        budget_rows = budgets.list_for_household(household_id)

        timestamps = [row.updated_at for row in inventory_rows if row.updated_at]
        last_updated = max(timestamps) if timestamps else datetime.now(UTC)
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=UTC)

        return HouseholdState(
            household_id=household_id,
            household=household,
            members=members.list_for_household(household_id),
            cook_profile=cook_rows[0] if cook_rows else None,
            inventory=inventory_rows,
            leftovers=leftovers.list_for_household(household_id),
            dish_history=history.list_for_household(household_id),
            budget=budget_rows[0] if budget_rows else None,
            preference_signals=signals.list_for_household(household_id),
            last_updated=last_updated,
        )

    def is_stale(self, household_id: int, max_age_seconds: int) -> bool:
        """Bible §4.3's recency-window check: state the planner should not
        act on a green order against if it's too old to trust."""
        state = self.get_household_state(household_id)
        age = datetime.now(UTC) - state.last_updated
        return age > timedelta(seconds=max_age_seconds)

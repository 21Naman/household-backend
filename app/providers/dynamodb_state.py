"""Ticket #32 -- DynamoDBStateStore.

Must satisfy exactly the contract app.core.state_store.LocalStateStore
does -- Ticket #37's parity suite runs the same assertions against both.
Single-table design keyed by household_id, storing one JSON blob per
household (inventory, members, cook profile, budget, dish history,
preference signals) plus a last_updated timestamp for the staleness check.

Without persistent state the system degrades into a stateless recipe
generator -- the exact failure mode Bible B021 rejects. If DynamoDB is
unavailable, is_stale() must return True (never silently trust unreadable
state) so the planner asks a human rather than guessing at inventory.

Requires live AWS credentials to exercise; code-complete, not
live-verified in this container. See Ticket #37.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from app.core.state_store import HouseholdState

UTC = timezone.utc


class DynamoDBUnavailable(RuntimeError):
    pass


class DynamoDBStateStore:
    def __init__(self, table_name: str, region: str):
        self.table_name = table_name
        self.region = region
        self._table = None

    def _get_table(self):
        if self._table is None:
            import boto3

            self._table = boto3.resource("dynamodb", region_name=self.region).Table(self.table_name)
        return self._table

    def _pk(self, household_id: int) -> str:
        return f"HOUSEHOLD#{household_id}"

    def put_household_state(self, state: HouseholdState) -> None:
        table = self._get_table()
        item = {
            "pk": self._pk(state.household_id),
            "sk": "STATE",
            "household_id": state.household_id,
            "payload": json.dumps(_serialize_state(state), default=str),
            "last_updated": state.last_updated.isoformat(),
        }
        table.put_item(Item=item)

    def get_household_state(self, household_id: int) -> HouseholdState:
        try:
            table = self._get_table()
            response = table.get_item(Key={"pk": self._pk(household_id), "sk": "STATE"})
        except Exception as exc:  # pragma: no cover - requires live AWS creds
            raise DynamoDBUnavailable(f"Could not read household state from DynamoDB: {exc}") from exc

        item = response.get("Item")
        if not item:
            # No state yet -- return an empty-but-valid state, timestamped
            # far in the past so is_stale() correctly reports it untrusted.
            return HouseholdState(household_id=household_id, household=None, last_updated=datetime(1970, 1, 1, tzinfo=UTC))

        payload = json.loads(item["payload"])
        last_updated = datetime.fromisoformat(item["last_updated"])
        return HouseholdState(household_id=household_id, household=None, last_updated=last_updated, **payload)

    def is_stale(self, household_id: int, max_age_seconds: int) -> bool:
        try:
            state = self.get_household_state(household_id)
        except DynamoDBUnavailable:
            return True  # fail safe: unreadable state is never trusted as fresh
        age = datetime.now(UTC) - state.last_updated
        return age > timedelta(seconds=max_age_seconds)


def _serialize_state(state: HouseholdState) -> dict:
    """Strip non-JSON-serializable SQLModel objects down to plain dicts.
    A real implementation would define an explicit DTO; this keeps the
    contract demonstrable without duplicating every model's shape here."""
    return {
        "members": [m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in state.members],
        "cook_profile": state.cook_profile.model_dump(mode="json") if state.cook_profile and hasattr(state.cook_profile, "model_dump") else None,
        "inventory": [i.model_dump(mode="json") if hasattr(i, "model_dump") else i for i in state.inventory],
        "leftovers": [l.model_dump(mode="json") if hasattr(l, "model_dump") else l for l in state.leftovers],
        "dish_history": [h.model_dump(mode="json") if hasattr(h, "model_dump") else h for h in state.dish_history],
        "budget": state.budget.model_dump(mode="json") if state.budget and hasattr(state.budget, "model_dump") else None,
        "preference_signals": [p.model_dump(mode="json") if hasattr(p, "model_dump") else p for p in state.preference_signals],
    }

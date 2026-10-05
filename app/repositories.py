"""Household-scoped repositories — Ticket #4.

`Repository.get(item_id)` used to fetch by primary key alone with no
ownership check. Some of these rows hold an encrypted payment mandate
(PineLabsConnection) and budget data, so that was a real cross-household
read/write risk. Every route that
touches a single record must now go through `get_for_household`, which
requires household_id as a normal (non-optional) parameter — omitting it is
a type error, not a silent bypass — and returns 404, not 403, on a mismatch
so the response doesn't confirm another household's record exists.
"""

from typing import Any, Generic, TypeVar

from fastapi import HTTPException, status
from sqlmodel import SQLModel, Session, select

T = TypeVar("T", bound=SQLModel)


class NotHouseholdScoped(RuntimeError):
    """Raised at call time (not just flagged by a type-checker comment) when
    list_for_household or get_for_household is used against a model that has
    no household_id column — e.g. DemoRecipe."""


class Repository(Generic[T]):
    def __init__(self, model: type[T], session: Session) -> None:
        self.model = model
        self.session = session

    def _require_household_scoped(self) -> None:
        if not hasattr(self.model, "household_id"):
            raise NotHouseholdScoped(
                f"{self.model.__name__} has no household_id column; "
                "use get()/list() directly for non-household-scoped models."
            )

    def get(self, item_id: int) -> T:
        """Unscoped fetch by primary key. Only safe for models that are not
        household-scoped in the first place (DemoRecipe), or where the id IS
        the household id (Household). Every other household-owned model
        should use get_for_household instead."""
        item = self.session.get(self.model, item_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{self.model.__name__} {item_id} was not found")
        return item

    def get_for_household(self, item_id: int, household_id: int) -> T:
        self._require_household_scoped()
        item = self.session.get(self.model, item_id)
        if item is None or getattr(item, "household_id", None) != household_id:
            # 404, not 403 — do not confirm existence of another household's record.
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{self.model.__name__} {item_id} was not found")
        return item

    def list_for_household(self, household_id: int) -> list[T]:
        self._require_household_scoped()
        return list(self.session.exec(select(self.model).where(self.model.household_id == household_id)))  # type: ignore[attr-defined]

    def create(self, item: T) -> T:
        self.session.add(item)
        self.session.commit()
        self.session.refresh(item)
        return item

    def update(self, item: T, values: dict[str, Any]) -> T:
        item.sqlmodel_update(values)
        self.session.add(item)
        self.session.commit()
        self.session.refresh(item)
        return item

    def update_for_household(self, item_id: int, household_id: int, values: dict[str, Any]) -> T:
        item = self.get_for_household(item_id, household_id)
        return self.update(item, values)

    def delete_for_household(self, item_id: int, household_id: int) -> None:
        item = self.get_for_household(item_id, household_id)
        self.session.delete(item)
        self.session.commit()

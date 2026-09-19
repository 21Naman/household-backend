from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import DemoRecipe, Household, InventoryLot
from app.repositories import NotHouseholdScoped, Repository


def test_get_for_household_succeeds_for_owner(session, household):
    repo = Repository(InventoryLot, session)
    lot = repo.create(InventoryLot(household_id=household.id, ingredient="Rice", quantity=1, unit="kg"))
    fetched = repo.get_for_household(lot.id, household.id)
    assert fetched.id == lot.id


def test_get_for_household_refuses_cross_household_as_404(session, household):
    other = Household(name="Other Household")
    session.add(other)
    session.commit()
    session.refresh(other)

    repo = Repository(InventoryLot, session)
    lot = repo.create(InventoryLot(household_id=household.id, ingredient="Rice", quantity=1, unit="kg"))

    with pytest.raises(HTTPException) as exc_info:
        repo.get_for_household(lot.id, other.id)
    assert exc_info.value.status_code == 404


def test_list_for_household_only_returns_owned_rows(session, household):
    other = Household(name="Other Household")
    session.add(other)
    session.commit()
    session.refresh(other)

    repo = Repository(InventoryLot, session)
    repo.create(InventoryLot(household_id=household.id, ingredient="Rice", quantity=1, unit="kg"))
    repo.create(InventoryLot(household_id=other.id, ingredient="Dal", quantity=1, unit="kg"))

    rows = repo.list_for_household(household.id)
    assert len(rows) == 1
    assert rows[0].ingredient == "Rice"


def test_non_household_scoped_model_raises_clear_error(session):
    repo = Repository(DemoRecipe, session)
    with pytest.raises(NotHouseholdScoped):
        repo.list_for_household(1)


def test_update_for_household_refuses_cross_household(session, household):
    other = Household(name="Other Household")
    session.add(other)
    session.commit()
    session.refresh(other)

    repo = Repository(InventoryLot, session)
    lot = repo.create(InventoryLot(household_id=household.id, ingredient="Rice", quantity=1, unit="kg"))

    with pytest.raises(HTTPException) as exc_info:
        repo.update_for_household(lot.id, other.id, {"quantity": 99})
    assert exc_info.value.status_code == 404
    session.refresh(lot)
    assert lot.quantity == 1  # unchanged

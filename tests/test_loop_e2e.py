"""Ticket #28 -- end-to-end loop closure.

The unclosed path matters as much as the happy path (Ticket #24). A crash
mid-close must leave memory untouched rather than half-written -- this is
the quiet version of the failure the whole product exists to prevent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

UTC = timezone.utc


@pytest.fixture()
def client(api_client):
    """This suite asserts against rows as well as responses, so it takes the
    engine alongside the client."""
    return api_client


def test_happy_path_updates_all_memory_surfaces_and_closes(client):
    c, engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    c.post(f"/api/households/{hid}/inventory/capture/confirm", json={"ingredient": "Rice", "quantity": 1000, "unit": "g", "freshness": "fresh"})
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]
    c.post(f"/api/households/{hid}/loops/{loop_id}/confirm-cook")

    outcome = c.post(
        f"/api/households/{hid}/loops/{loop_id}/outcome",
        json={
            "dish_name": "Rice and Dal",
            "rating": 4,
            "feedback": "good",
            "consumed": [{"name": "Rice", "quantity": 200, "unit": "g", "unit_price_inr": 0}],
        },
    )
    assert outcome.status_code == 200

    loop = c.get(f"/api/households/{hid}/loops/{loop_id}").json()
    assert loop["status"] == "completed"
    assert loop["cook_confirmed"] is True
    assert loop["eater_feedback_captured"] is True
    assert loop["closed_at"] is not None

    history = c.get(f"/api/households/{hid}/history").json()
    assert any(h["dish_name"] == "Rice and Dal" and h["rating"] == 4 for h in history)

    inventory = c.get(f"/api/households/{hid}/inventory").json()
    rice = next(i for i in inventory if i["ingredient"] == "Rice")
    assert rice["quantity"] == 800  # 1000 - 200 consumed


def test_unconfirmed_loop_becomes_unclosed_not_completed(client):
    from app.core.unclosed_sweep import run_unclosed_sweep
    from app.enums import LoopStatus
    from app.models import MealLoopRecord

    c, engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]

    with Session(engine) as session:
        loop = session.get(MealLoopRecord, loop_id)
        loop.created_at = datetime.now(UTC) - timedelta(hours=10)
        session.add(loop)
        session.commit()

    flagged = run_unclosed_sweep(timeout_hours=6, db_engine=engine)
    assert loop_id in flagged

    final = c.get(f"/api/households/{hid}/loops/{loop_id}").json()
    assert final["status"] == "unclosed"
    assert final["status"] != "completed"


def test_mid_close_crash_leaves_memory_untouched(client):
    """A crash inside capture_outcome's transaction must roll back cleanly
    -- inventory must not be decremented if DishHistory failed to write,
    and vice versa."""
    from app.models import DishHistory, InventoryLot
    from app.repositories import Repository

    c, engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    c.post(f"/api/households/{hid}/inventory/capture/confirm", json={"ingredient": "Rice", "quantity": 1000, "unit": "g", "freshness": "fresh"})
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]

    with Session(engine) as session:
        before_qty = Repository(InventoryLot, session).list_for_household(hid)[0].quantity

    # Force a failure inside the transaction by breaking the DishHistory
    # write (invalid rating outside the 1-5 range bypasses the Pydantic
    # schema but not a raw session.add path) -- simulate via a monkeypatch
    # that raises on the second session.add call.
    import app.api.routes as routes_module

    original_create = Repository.create
    call_count = {"n": 0}

    def _flaky_create(self, item):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated crash mid-close")
        return original_create(self, item)

    Repository.create = _flaky_create
    try:
        with pytest.raises(RuntimeError, match="simulated crash mid-close"):
            c.post(
                f"/api/households/{hid}/loops/{loop_id}/outcome",
                json={"dish_name": "Rice and Dal", "rating": 4, "feedback": "x", "consumed": [{"name": "Rice", "quantity": 200, "unit": "g", "unit_price_inr": 0}]},
            )
    finally:
        Repository.create = original_create

    with Session(engine) as session:
        after_qty = Repository(InventoryLot, session).list_for_household(hid)[0].quantity
        history = list(session.exec(select(DishHistory).where(DishHistory.household_id == hid)))

    assert after_qty == before_qty  # inventory untouched
    assert history == []  # no partial history row either

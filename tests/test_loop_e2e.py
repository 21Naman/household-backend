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
            "consumed": [{"name": "Rice", "quantity": 200, "unit": "g"}],
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
    from app.enums import LoopStatus
    from app.models import DishHistory, InventoryLot, MealLoopRecord
    from app.repositories import Repository

    c, engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    c.post(f"/api/households/{hid}/inventory/capture/confirm", json={"ingredient": "Rice", "quantity": 1000, "unit": "g", "freshness": "fresh"})
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]

    with Session(engine) as session:
        before_qty = Repository(InventoryLot, session).list_for_household(hid)[0].quantity

    # Crash AFTER the history row is staged and BEFORE the commit -- the
    # inventory deduction is the first thing that runs in that window.
    #
    # This is deliberately not a crash on the history write itself. The
    # earlier version of this test raised on the first Repository.create,
    # which was the history row, so it never reached the window where the
    # two writes could disagree. capture_outcome used to call
    # Repository.create for DishHistory, and that helper commits internally
    # -- so a failure here left a committed orphan history row for a loop
    # that never closed, which session.rollback() could not undo, directly
    # contradicting the handler's "one transaction" docstring. Staging the
    # row with session.add instead puts it under the single commit at the
    # end. Fail this test and that regression is back.
    original_list = Repository.list_for_household

    def _flaky_list(self, household_id):
        if self.model is InventoryLot:
            raise RuntimeError("simulated crash mid-close")
        return original_list(self, household_id)

    Repository.list_for_household = _flaky_list
    try:
        with pytest.raises(RuntimeError, match="simulated crash mid-close"):
            c.post(
                f"/api/households/{hid}/loops/{loop_id}/outcome",
                json={"dish_name": "Rice and Dal", "rating": 4, "feedback": "x", "consumed": [{"name": "Rice", "quantity": 200, "unit": "g"}]},
            )
    finally:
        Repository.list_for_household = original_list

    with Session(engine) as session:
        after_qty = Repository(InventoryLot, session).list_for_household(hid)[0].quantity
        history = list(session.exec(select(DishHistory).where(DishHistory.household_id == hid)))
        loop = session.get(MealLoopRecord, loop_id)

    assert after_qty == before_qty  # inventory untouched
    assert history == []  # and no orphan history row for a loop that never closed
    assert loop.status != LoopStatus.COMPLETED  # the loop is still open
    assert loop.closed_at is None


# The closure path had no coverage for any of the cases below, which is how
# the silent-deduction behaviour stayed invisible. These pin what the
# endpoint actually does today, so the UI's warnings can be written against
# real behaviour rather than assumed behaviour.


def test_a_mishap_still_closes_the_loop_but_records_it_as_not_accepted(client):
    c, _engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]

    response = c.post(
        f"/api/households/{hid}/loops/{loop_id}/outcome",
        json={"dish_name": "Burnt Khichdi", "rating": 2, "feedback": "stuck to the pan",
              "mishap": True, "leftovers_portions": 1.5, "consumed": []},
    )

    assert response.status_code == 200
    assert c.get(f"/api/households/{hid}/loops/{loop_id}").json()["status"] == "completed"
    entry = next(h for h in c.get(f"/api/households/{hid}/history").json()
                 if h["dish_name"] == "Burnt Khichdi")
    assert entry["accepted"] is False
    assert entry["leftovers_portions"] == 1.5


def test_a_consumed_name_matching_no_lot_is_reported_rather_than_ignored(client):
    """Documents a real sharp edge rather than endorsing it: the deduction
    matches lots by exact name after strip/lower, so 'Basmati Rice' will not
    find a 'Rice' lot. Nothing is deducted and nothing errors -- but the
    response now names the skip instead of returning a bare success, so a
    caller can tell the difference between "deducted" and "silently did
    nothing"."""
    c, _engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    c.post(f"/api/households/{hid}/inventory/capture/confirm",
           json={"ingredient": "Rice", "quantity": 1000, "unit": "g", "freshness": "fresh"})
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]

    response = c.post(
        f"/api/households/{hid}/loops/{loop_id}/outcome",
        json={"dish_name": "Pulao", "consumed": [{"name": "Basmati Rice", "quantity": 200, "unit": "g"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deducted"] == []
    assert body["skipped"][0]["reason"] == "not_in_inventory"
    rice = next(i for i in c.get(f"/api/households/{hid}/inventory").json() if i["ingredient"] == "Rice")
    assert rice["quantity"] == 1000  # untouched: the name did not match


def test_consuming_more_than_is_in_stock_floors_at_zero_and_reports_the_shortfall(client):
    c, _engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    c.post(f"/api/households/{hid}/inventory/capture/confirm",
           json={"ingredient": "Rice", "quantity": 300, "unit": "g", "freshness": "fresh"})
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]

    response = c.post(
        f"/api/households/{hid}/loops/{loop_id}/outcome",
        json={"dish_name": "Khichdi", "consumed": [{"name": "Rice", "quantity": 5000, "unit": "g"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["skipped"][0]["reason"] == "insufficient_stock"
    assert body["skipped"][0]["shortfall"] == 4700
    rice = next(i for i in c.get(f"/api/households/{hid}/inventory").json() if i["ingredient"] == "Rice")
    assert rice["quantity"] == 0  # drained, never negative


def test_a_unit_mismatch_is_refused_by_the_server_even_when_the_client_asks_for_it(client):
    """The rule has to live where the write happens.

    A recipe asking for 200 g against a lot counted in whole tomatoes used to
    deduct min(3, 200) and leave the kitchen showing Tomato: 3 -> 0. The
    client warns about this, but a warning is not a rule -- anyone can skip
    the page.
    """
    c, _engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    c.post(f"/api/households/{hid}/inventory/capture/confirm",
           json={"ingredient": "Tomato", "quantity": 3, "unit": "count", "freshness": "fresh"})
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]

    response = c.post(
        f"/api/households/{hid}/loops/{loop_id}/outcome",
        json={"dish_name": "Curry", "consumed": [{"name": "Tomato", "quantity": 200, "unit": "g"}]},
    )

    assert response.status_code == 200
    skipped = response.json()["skipped"][0]
    assert skipped["reason"] == "unit_mismatch"
    assert skipped["stock_quantity"] == 3
    assert skipped["stock_unit"] == "count"
    tomato = next(i for i in c.get(f"/api/households/{hid}/inventory").json() if i["ingredient"] == "Tomato")
    assert tomato["quantity"] == 3, "nothing may be deducted across incomparable units"


def test_a_comparable_unit_deducts_after_converting(client):
    """The other half of sharing one converter with compute_ingredient_gap: a
    2 kg lot counts as stock for a recipe asking 400 g, so it must also be
    deductible for one. Strict string matching would have counted it and then
    refused to spend it."""
    c, _engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    c.post(f"/api/households/{hid}/inventory/capture/confirm",
           json={"ingredient": "Rice", "quantity": 2, "unit": "kg", "freshness": "fresh"})
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]

    response = c.post(
        f"/api/households/{hid}/loops/{loop_id}/outcome",
        json={"dish_name": "Pulao", "consumed": [{"name": "Rice", "quantity": 400, "unit": "g"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["skipped"] == []
    assert body["deducted"][0] == {
        "lot_id": body["deducted"][0]["lot_id"],
        "ingredient": "Rice",
        "before": 2.0,
        "after": 1.6,
        "unit": "kg",
    }
    rice = next(i for i in c.get(f"/api/households/{hid}/inventory").json() if i["ingredient"] == "Rice")
    assert rice["quantity"] == pytest.approx(1.6)


def test_outcome_closes_a_loop_even_without_a_prior_confirm_cook(client):
    """The UI panel posts only /outcome, so this is the path it actually
    takes. capture_outcome sets cook_confirmed itself -- there is no ordering
    guard anywhere -- and this test pins that so the panel is not relying on
    undocumented behaviour."""
    c, _engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]

    response = c.post(
        f"/api/households/{hid}/loops/{loop_id}/outcome",
        json={"dish_name": "Dal Chawal", "rating": 5, "consumed": []},
    )

    assert response.status_code == 200
    loop = c.get(f"/api/households/{hid}/loops/{loop_id}").json()
    assert loop["cook_confirmed"] is True
    assert loop["eater_feedback_captured"] is True
    assert loop["status"] == "completed"


def test_a_zero_quantity_consumed_row_is_rejected(client):
    """ConsumedItem.quantity is gt=0, so the UI must drop empty rows rather
    than sending zeros -- a whole submission would 422 on one blank field."""
    c, _engine = client
    hid = c.post("/api/households", json={"name": "Test HH"}).json()["id"]
    loop_id = c.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]

    response = c.post(
        f"/api/households/{hid}/loops/{loop_id}/outcome",
        json={"dish_name": "Dal", "consumed": [{"name": "Rice", "quantity": 0, "unit": "g"}]},
    )

    assert response.status_code == 422

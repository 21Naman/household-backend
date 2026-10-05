"""app/demo_seed.py — the data a deployed instance demos against.

These exist because the demo seeder moved from a run-once script into a
long-lived process that re-runs it every few hours. That change turns three
things that never mattered into things that do: the spec dicts must survive
repeated reads, dates must be re-derived rather than frozen at import, and a
refresh must restore rows without deleting the ids a judge is mid-session on.
"""
from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session, select

from app.demo_seed import HOUSEHOLDS, refresh_or_create, run_demo_seed, seed
from app.enums import FreshnessState
from app.models import AuditEvent, Budget, InventoryLot, MealLoopRecord, PreferenceSignal
from app.services import effective_freshness


def _seed_all(session: Session) -> None:
    for spec in HOUSEHOLDS:
        seed(session, spec)


def _lots(session: Session) -> list[InventoryLot]:
    return list(session.exec(select(InventoryLot)))


def test_seeding_then_refreshing_twice_never_mutates_the_specs(session):
    """The regression test for the .pop() bug.

    `expires_in_days` and `served_days_ago` used to be popped off the
    module-level spec dicts, so the second call in a process raised KeyError.
    That was survivable in a one-shot script and fatal for a reseed job.
    """
    snapshot = copy.deepcopy(HOUSEHOLDS)
    _seed_all(session)
    for spec in HOUSEHOLDS:
        refresh_or_create(session, spec)
    for spec in HOUSEHOLDS:
        refresh_or_create(session, spec)

    assert HOUSEHOLDS == snapshot


def test_every_household_carries_exactly_one_already_expired_signal(session):
    """The visible proof that the agent manages its own memory.

    Without a deliberately expired signal the expiry rule is correct and
    invisible, because the refresh job keeps pushing every live signal's date
    forward.
    """
    _seed_all(session)
    today = date.today()

    expired = [
        signal
        for signal in session.exec(select(PreferenceSignal))
        if signal.expires_on is not None and signal.expires_on < today
    ]
    by_household: dict[int, int] = {}
    for signal in expired:
        by_household[signal.household_id] = by_household.get(signal.household_id, 0) + 1

    assert len(by_household) == len(HOUSEHOLDS)
    assert set(by_household.values()) == {1}


def test_cook_skill_signals_do_not_expire(session):
    """How skilled the cook is is a standing property of a person, not an
    event. It previously carried a 30-day expiry, which would have quietly
    stopped telling the model that Rehana needs step-by-step dishes."""
    _seed_all(session)
    signal = session.exec(
        select(PreferenceSignal).where(PreferenceSignal.signal.contains("Rehana is new to biryani"))
    ).first()

    assert signal is not None
    assert signal.expires_on is None


def test_refresh_restores_drained_inventory_without_changing_lot_ids(session):
    _seed_all(session)
    before = {lot.id: lot.quantity for lot in _lots(session)}

    for lot in _lots(session):
        lot.quantity = 0.0
        session.add(lot)
    session.commit()

    for spec in HOUSEHOLDS:
        refresh_or_create(session, spec)

    after = {lot.id: lot.quantity for lot in _lots(session)}
    assert after.keys() == before.keys(), "refresh must not delete and recreate lots"
    assert after == before


def test_refresh_pushes_updated_at_forward_so_stock_does_not_go_stale(session):
    """The test that proves the job earns its existence.

    A lot older than inventory_recency_window_hours reads STALE regardless of
    its expiry date, drops out of usable stock, and recipe generation starts
    failing the availability check for no visible reason.
    """
    _seed_all(session)
    stale_moment = datetime.now(UTC) - timedelta(hours=72)
    for lot in _lots(session):
        lot.updated_at = stale_moment
        session.add(lot)
    session.commit()

    assert all(
        effective_freshness(lot, recency_window_hours=48) is FreshnessState.STALE
        for lot in _lots(session)
    )

    for spec in HOUSEHOLDS:
        refresh_or_create(session, spec)

    assert not any(
        effective_freshness(lot, recency_window_hours=48) is FreshnessState.STALE
        for lot in _lots(session)
    )


def test_refresh_preserves_loops_and_audit_events(session):
    """A judge mid-loop must not be broken by the reseed job. Loops, approvals
    and audit rows are the record of what happened; a demo-data job has no
    business rewriting history."""
    _seed_all(session)
    loop = MealLoopRecord(household_id=1, trigger_type="manual")
    session.add(loop)
    session.add(AuditEvent(household_id=1, event="recipe_generated", detail="test"))
    session.commit()
    session.refresh(loop)
    loop_id = loop.id

    for spec in HOUSEHOLDS:
        refresh_or_create(session, spec)

    assert session.get(MealLoopRecord, loop_id) is not None
    assert len(list(session.exec(select(AuditEvent)))) == 1


def test_refresh_leaves_rows_a_judge_added_alone(session):
    """This restores the seeded rows; it does not assert ownership of the
    household."""
    _seed_all(session)
    session.add(InventoryLot(household_id=1, ingredient="Saffron", quantity=5, unit="g"))
    session.commit()

    for spec in HOUSEHOLDS:
        refresh_or_create(session, spec)

    survivor = session.exec(
        select(InventoryLot).where(InventoryLot.ingredient == "Saffron")
    ).first()
    assert survivor is not None
    assert survivor.quantity == 5


def test_refresh_resets_a_spent_budget(session):
    _seed_all(session)
    budget = session.exec(select(Budget).where(Budget.household_id == 1)).first()
    seeded_limit = budget.monthly_limit
    budget.spent_amount = budget.monthly_limit
    session.add(budget)
    session.commit()

    for spec in HOUSEHOLDS:
        refresh_or_create(session, spec)

    budget = session.exec(select(Budget).where(Budget.household_id == 1)).first()
    assert budget.monthly_limit == seeded_limit
    assert budget.spent_amount < budget.monthly_limit


def test_refresh_creates_a_household_that_is_missing(session):
    assert refresh_or_create(session, HOUSEHOLDS[0]) == (HOUSEHOLDS[0]["household"]["name"], "created")
    assert refresh_or_create(session, HOUSEHOLDS[0]) == (HOUSEHOLDS[0]["household"]["name"], "refreshed")


def test_run_demo_seed_accepts_an_engine_override_and_is_repeatable(engine):
    """The scheduler calls this with no arguments; tests pass an engine.

    The engine is resolved through the module rather than bound at import
    because conftest reassigns app.database.engine per test.
    """
    first = run_demo_seed(db_engine=engine)
    second = run_demo_seed(db_engine=engine)

    assert [action for _, action in first] == ["created"] * len(HOUSEHOLDS)
    assert [action for _, action in second] == ["refreshed"] * len(HOUSEHOLDS)

    with Session(engine) as session:
        assert len(list(session.exec(select(InventoryLot)))) == len(
            [lot for spec in HOUSEHOLDS for lot in spec["inventory"]]
        )

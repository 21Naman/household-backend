from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.core.unclosed_sweep import run_unclosed_sweep
from app.enums import LoopStatus
from app.models import AuditEvent, MealLoopRecord

UTC = timezone.utc


def test_backdated_unconfirmed_loop_is_flagged_unclosed(engine, household):
    from sqlmodel import Session

    with Session(engine) as session:
        loop = MealLoopRecord(household_id=household.id, trigger_type="manual", status=LoopStatus.TRIGGERED)
        session.add(loop)
        session.commit()
        session.refresh(loop)
        loop.created_at = datetime.now(UTC) - timedelta(hours=10)
        session.add(loop)
        session.commit()
        loop_id = loop.id

    flagged = run_unclosed_sweep(timeout_hours=6, db_engine=engine)
    assert loop_id in flagged

    with Session(engine) as session:
        refreshed = session.get(MealLoopRecord, loop_id)
        assert refreshed.status == LoopStatus.UNCLOSED
        assert refreshed.unclosed_reason
        assert refreshed.closed_at is not None


def test_confirmed_loop_within_window_is_untouched(engine, household):
    from sqlmodel import Session

    with Session(engine) as session:
        loop = MealLoopRecord(household_id=household.id, trigger_type="manual", status=LoopStatus.COMPLETED, cook_confirmed=True, eater_feedback_captured=True)
        session.add(loop)
        session.commit()
        loop_id = loop.id

    flagged = run_unclosed_sweep(timeout_hours=6, db_engine=engine)
    assert loop_id not in flagged

    with Session(engine) as session:
        refreshed = session.get(MealLoopRecord, loop_id)
        assert refreshed.status == LoopStatus.COMPLETED


def test_sweep_writes_audit_trail_even_when_nothing_flagged(engine, household):
    from sqlmodel import Session

    with Session(engine) as session:
        loop = MealLoopRecord(household_id=household.id, trigger_type="manual", status=LoopStatus.TRIGGERED)
        session.add(loop)
        session.commit()

    flagged = run_unclosed_sweep(timeout_hours=6, db_engine=engine)
    assert flagged == []  # loop is fresh, within the timeout window

    with Session(engine) as session:
        events = list(session.exec(select(AuditEvent).where(AuditEvent.event == "unclosed_sweep_ran")))
        assert len(events) >= 1  # the sweep's own run is auditable even with nothing to flag


def test_sweep_distinguishes_no_confirmation_from_no_feedback(engine, household):
    from sqlmodel import Session

    with Session(engine) as session:
        loop = MealLoopRecord(household_id=household.id, trigger_type="manual", status=LoopStatus.COOKING, cook_confirmed=True)
        session.add(loop)
        session.commit()
        session.refresh(loop)
        loop.created_at = datetime.now(UTC) - timedelta(hours=10)
        session.add(loop)
        session.commit()
        loop_id = loop.id

    run_unclosed_sweep(timeout_hours=6, db_engine=engine)

    with Session(engine) as session:
        refreshed = session.get(MealLoopRecord, loop_id)
        assert "feedback" in refreshed.unclosed_reason

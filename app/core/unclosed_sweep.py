"""Ticket #24 -- unclosed-loop detection.

Bible §4.3 Termination conditions: silently assuming success "would
quietly recreate the 'no shared memory' problem the whole product exists
to solve." A loop closes ONLY on cook confirmation AND eater feedback
(see app.api.routes.capture_outcome). Anything else past the timeout
window becomes UNCLOSED, never COMPLETED -- and this sweep writes an
AuditEvent every time it runs, even when it flags nothing, so a silently
non-running sweep is itself detectable (its own absence leaves a gap in
the audit trail).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.database import engine
from app.enums import LoopStatus
from app.models import AuditEvent, MealLoopRecord

UTC = timezone.utc


def run_unclosed_sweep(timeout_hours: int, db_engine=None) -> list[int]:
    """Returns the list of loop IDs newly flagged unclosed on this run.
    Accepts an optional engine override for testability, matching the
    pattern app.database.initialize_database already uses."""
    active_engine = db_engine or engine
    cutoff = datetime.now(UTC) - timedelta(hours=timeout_hours)
    flagged: list[int] = []
    scanned_households: set[int] = set()

    with Session(active_engine) as session:
        open_loops = list(
            session.exec(
                select(MealLoopRecord).where(
                    MealLoopRecord.status.not_in(list(LoopStatus.terminal()))  # type: ignore[attr-defined]
                )
            )
        )
        for loop in open_loops:
            scanned_households.add(loop.household_id)
            created_at = loop.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at >= cutoff:
                continue  # still within the timeout window

            reason = "timed out with no cook confirmation" if not loop.cook_confirmed else "timed out with no eater feedback"
            loop.status = LoopStatus.UNCLOSED
            loop.unclosed_reason = reason
            loop.closed_at = datetime.now(UTC)
            session.add(loop)
            flagged.append(loop.id)
            session.add(
                AuditEvent(
                    household_id=loop.household_id,
                    meal_loop_id=loop.id,
                    event="loop_unclosed",
                    detail=reason,
                )
            )

        # One "the sweep ran" event per household that had an open loop to
        # scan -- written even when nothing was flagged, so a non-running
        # sweep is detectable by its own absence from the audit trail.
        for household_id in scanned_households:
            session.add(
                AuditEvent(
                    household_id=household_id,
                    meal_loop_id=None,
                    event="unclosed_sweep_ran",
                    detail=f"cutoff={cutoff.isoformat()}",
                )
            )
        session.commit()

    return flagged

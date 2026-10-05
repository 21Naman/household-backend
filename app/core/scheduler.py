"""Ticket #17 — LocalScheduler (EventScheduler over APScheduler).

Changed from the original scheduler.py:
  - Wrapped behind the EventScheduler protocol so Ticket #35's
    EventBridgeScheduler is a swap, not a rewrite.
  - scheduled_trigger's DB write is now wrapped in error handling that
    writes an AuditEvent on failure -- previously a raised exception was
    only visible in APScheduler's own internal log.
  - Registers all three trigger classes: scheduled (daily check-in +
    Ticket #24's unclosed-loop sweep + Ticket #36's weekly reflection),
    reactive (guest/mishap/low-stock events), and manual (unchanged).
  - Preserves the existing degradation message -- "use the manual trigger
    instead" is good behavior and is now surfaced through the API (see
    app/api/routes.py) rather than only logged.

The registration logic lives on LocalScheduler itself. It previously sat in
module-level functions that existed only to be wrapped by one-line methods,
which meant two names for every operation. Only the APScheduler instance and
its process-wide start/stop remain at module level, because the FastAPI
lifespan owns them and they are genuinely per-process, not per-object.
"""
from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session

from app.database import engine
from app.enums import LoopStatus, TaskStatus
from app.models import AuditEvent, LocalTask, MealLoopRecord

UTC = timezone.utc

scheduler = BackgroundScheduler()
_scheduler_error: str | None = None

_UNAVAILABLE = "Automatic local triggers are unavailable; use the manual trigger instead."


def start_scheduler() -> None:
    global _scheduler_error
    if scheduler.running:
        return
    try:
        scheduler.start()
        _scheduler_error = None
    except Exception as exc:
        _scheduler_error = str(exc)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def scheduled_trigger(household_id: int) -> int | None:
    """Daily check-in, run as an APScheduler job. Wrapped so a DB failure
    writes an AuditEvent instead of vanishing into APScheduler's internal
    logging."""
    try:
        with Session(engine) as session:
            loop = MealLoopRecord(household_id=household_id, trigger_type="scheduled", status=LoopStatus.TRIGGERED)
            session.add(loop)
            session.commit()
            session.refresh(loop)
            session.add(
                LocalTask(
                    household_id=household_id,
                    meal_loop_id=loop.id,
                    task_type="daily_meal_check",
                    status=TaskStatus.PENDING,
                    details="Scheduled local planning reminder",
                )
            )
            session.add(AuditEvent(household_id=household_id, meal_loop_id=loop.id, event="scheduled_trigger", detail="local task created"))
            session.commit()
            return loop.id
    except Exception as exc:  # pragma: no cover - defensive; DB failure path
        try:
            with Session(engine) as session:
                session.add(
                    AuditEvent(
                        household_id=household_id,
                        meal_loop_id=None,
                        event="scheduled_trigger_failed",
                        detail=str(exc),
                    )
                )
                session.commit()
        except Exception:
            pass  # last resort: even the failure-audit write failed; nothing more we can do locally
        return None


class LocalScheduler:
    """EventScheduler implementation used in app/main.py."""

    def status(self) -> tuple[str, str | None]:
        if scheduler.running:
            return "available", None
        return "unavailable", _scheduler_error or _UNAVAILABLE

    def _require_running(self) -> None:
        if not scheduler.running:
            raise RuntimeError(self.status()[1])

    def register_daily_trigger(self, household_id: int, hour: int = 9) -> None:
        self._require_running()
        try:
            scheduler.add_job(
                scheduled_trigger,
                "cron",
                args=[household_id],
                hour=hour,
                id=f"daily-{household_id}",
                replace_existing=True,
            )
        except Exception as exc:
            raise RuntimeError(f"{_UNAVAILABLE} {exc}") from exc

    def register_sweep(self, name: str, interval_seconds: int, callback) -> None:
        """Ticket #24's unclosed-loop sweep and Ticket #36's weekly
        reflection both register here. `callback` takes no arguments and is
        responsible for its own session management."""
        self._require_running()
        scheduler.add_job(callback, "interval", seconds=interval_seconds, id=name, replace_existing=True)

    def emit_reactive(self, household_id: int, event_type: str, payload: dict) -> None:
        """BUILD IT has no real event bus -- reactive triggers are handled
        synchronously by the route that detects them (e.g. a guest-arrival
        POST creates its own MealLoopRecord directly). This method exists
        so callers written against the EventScheduler interface work
        unchanged once Ticket #35's EventBridgeScheduler adds a real bus;
        for now it just writes an audit trail entry."""
        with Session(engine) as session:
            session.add(
                AuditEvent(
                    household_id=household_id,
                    meal_loop_id=None,
                    event=f"reactive:{event_type}",
                    detail=str(payload),
                )
            )
            session.commit()

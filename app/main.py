from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.settings import get_settings

from app.api.routes import router
from app.api.routes_crud import router as crud_router
from app.database import initialize_database
from app.core.container import get_container
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.unclosed_sweep import run_unclosed_sweep
from app.demo_seed import run_demo_seed

logger = logging.getLogger("household_agent")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    for warning in settings.startup_warnings():
        logger.warning(warning)

    # Ticket #7's guard, in the one place every start path reaches. run()
    # below carries the original bind_host check, but run() only executes
    # under `python -m app.main`; a platform starting this as
    # `uvicorn app.main:app` imports the module and skips it entirely, which
    # meant the check protected local development and nothing else. The
    # lifespan handler re-reads settings on every TestClient entry too, so
    # unlike a module-scope check this one is actually testable.
    error = settings.public_deployment_error()
    if error:
        raise RuntimeError(error)

    initialize_database()

    container = get_container()
    start_scheduler()
    try:
        container.scheduler.register_sweep(
            "unclosed-loop-sweep",
            interval_seconds=settings.unclosed_sweep_interval_seconds,
            callback=lambda: run_unclosed_sweep(settings.loop_unclosed_timeout_hours),
        )
    except RuntimeError as exc:
        logger.warning("Could not register unclosed-loop sweep: %s", exc)

    if settings.demo_seed_on_startup:
        # The deployed image ships with no database at all (data/*.db is
        # gitignored and the container filesystem is ephemeral), so without
        # this the Space serves an empty household picker.
        try:
            for name, action in run_demo_seed():
                logger.info("demo seed: %s %s", action, name)
        except Exception:
            # Demo data failing must never stop the app from booting: the API
            # and the UI are still correct against an empty database.
            logger.warning("Demo seeding failed at startup", exc_info=True)
        try:
            container.scheduler.register_sweep(
                "demo-reseed",
                interval_seconds=settings.demo_reseed_interval_seconds,
                callback=run_demo_seed,
            )
        except RuntimeError as exc:
            logger.warning("Could not register the demo reseed job: %s", exc)

    yield

    stop_scheduler()


app = FastAPI(title="Household Agent", version="0.2.0", lifespan=lifespan)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_origin_regex=_settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(crud_router, prefix="/api")
app.include_router(router, prefix="/api")



@app.get("/health")
def health():
    return {"status": "ok"}


# The demo UI. Mounted AFTER every route above, because a mount at "/"
# matches anything unclaimed before it and would otherwise swallow /health
# and /docs. Served same-origin, so the CORS allowlist does not apply to it.
# This is the only static mount in the application.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")


def run() -> None:
    """Ticket #7: refuse to bind publicly with no API key configured."""
    import uvicorn

    settings = get_settings()
    host = settings.bind_host
    if not settings.api_key and host not in ("127.0.0.1", "localhost"):
        raise RuntimeError(
            "Refusing to bind to a non-loopback host with no HOUSEHOLD_API_KEY configured. "
            "Set HOUSEHOLD_API_KEY or bind to 127.0.0.1."
        )
    # Redundant with the lifespan check on purpose: this one fails before the
    # socket is opened, which is the better failure when we control the start.
    error = settings.public_deployment_error()
    if error:
        raise RuntimeError(error)
    uvicorn.run(app, host=host, port=settings.resolved_port())


if __name__ == "__main__":
    run()

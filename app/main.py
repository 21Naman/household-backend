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

logger = logging.getLogger("household_agent")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    for warning in settings.startup_warnings():
        logger.warning(warning)

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
    uvicorn.run(app, host=host, port=8000)


if __name__ == "__main__":
    run()

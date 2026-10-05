"""Auth gate — Ticket #7.

No authentication exists anywhere in the original backend, which is fine on
localhost and not fine the moment a demo URL exists: PineLabsConnection
holds a mandate token capable of debiting against a reserved ceiling, and
Budget holds real spend figures. This is deliberately minimal — a shared
secret, not a user system — because the Bible's MVP is a single household's
demo, not a multi-tenant product.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session

from app.database import get_session
from app.settings import Settings, get_settings


def require_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.api_key:
        # No key configured: acceptable only because main.py refuses to bind
        # publicly in this state (see settings.startup_warnings + main.py).
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key")


def get_db_session() -> Session:
    """Re-exported so route modules have one obvious import for the session
    dependency, matching the pattern the rest of the codebase already uses."""
    yield from get_session()

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401 registers tables


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture()
def household(session):
    from app.models import Household

    h = Household(name="Test Household")
    session.add(h)
    session.commit()
    session.refresh(h)
    return h


@contextmanager
def _api_client(monkeypatch, **env):
    """A TestClient over the real app, against a throwaway SQLite file.

    Every lru_cache and singleton that an earlier test may have populated
    against a different database URL has to be reset here, in this order --
    settings first, then the engine that reads them, then the container that
    holds providers built from both. Getting the order wrong gives a client
    that silently talks to the previous test's database.

    Yields (client, engine) so a test can assert against rows directly.
    """
    db_path = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("HOUSEHOLD_DATABASE_URL", f"sqlite:///{db_path}")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import app.settings as settings_module

    settings_module.get_settings.cache_clear()
    import app.database as database_module

    database_module.engine = database_module.create_database_engine()
    import app.core.container as container_module

    container_module.reset_container()

    from app.database import initialize_database
    from app.main import app

    initialize_database()

    from fastapi.testclient import TestClient

    try:
        with TestClient(app) as client:
            yield client, database_module.engine
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


@pytest.fixture()
def api_client(monkeypatch):
    """The default client: Zepto mocked on, so commerce quoting is live."""
    with _api_client(monkeypatch, HOUSEHOLD_ZEPTO_MOCK_ENABLED="true") as pair:
        yield pair


@pytest.fixture()
def api_client_factory(monkeypatch):
    """For tests that need different provider settings than the default."""

    @contextmanager
    def build(**env):
        with _api_client(monkeypatch, **env) as pair:
            yield pair

    return build

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


@pytest.fixture()
def red_tier_loop(api_client):
    """A planned loop whose basket genuinely prices into the red tier.

    Shared rather than inlined because the spend-gate tests, the
    stale-approval test and the payments-gate test all need the identical
    arrangement, and duplicating the pricing arithmetic across files is how
    two copies of it drift apart.

    The basket is sized from the settings, not from a literal: the red floor
    is a named threshold and this fixture has to track it. CommerceMockProvider
    prices a line at roughly ₹20 per 100 units, so the quantity is derived
    from the floor rather than guessed at.

    The monthly limit is deliberately generous. A basket that also broke the
    budget would be refused for two independent reasons at once, and the test
    could not tell which rule fired.

    Returns (household_id, loop_id, plan_response).
    """
    import app.settings as settings_module

    client, _engine = api_client
    settings = settings_module.get_settings()

    hid = client.post("/api/households", json={"name": "Big Basket Household"}).json()["id"]
    client.put(
        f"/api/households/{hid}/budget",
        json={"monthly_limit": settings.spend_tier_red_floor_inr * 20, "spent_amount": 0, "planned_amount": 0},
    )

    # ~₹20 per 100 g, so this clears the red floor with margin to spare even
    # at the low end of the mock's deterministic price variance.
    quantity = settings.spend_tier_red_floor_inr * 8
    client.post(
        "/api/dishes",
        json={
            "name": "Saffron Feast",
            "ingredients": [{"ingredient": "Saffron", "quantity": quantity, "unit": "g"}],
            "prep_minutes": 20,
            "servings": 2,
        },
    )

    loop_id = client.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]
    plan = client.post(
        f"/api/households/{hid}/loops/{loop_id}/plan",
        json={"servings": 2, "available_minutes": 30},
    ).json()
    return hid, loop_id, plan

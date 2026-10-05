from __future__ import annotations

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.settings import Settings, get_settings


def create_database_engine(settings: Settings | None = None):
    settings = settings or get_settings()
    database_path = settings.database_path
    if database_path:
        database_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    )


engine = create_database_engine()


def initialize_database(database_engine=None) -> None:
    """Ticket #3: the hand-rolled `ALTER TABLE inventory_lots ADD COLUMN
    freshness ...` migration from the original codebase is retired. That
    pattern does not scale — this build map alone adds columns to
    MealLoopRecord (#23, #24), ApprovalRequest (#26), and a whole new
    PineLabsConnection table (#18).

    Two paths now:
      - A FRESH database (no tables yet, e.g. tests, a new dev machine):
        `SQLModel.metadata.create_all` creates every table with every
        column already correct, straight from models.py. No migration
        needed because there is no existing data to preserve.
      - An EXISTING populated database (a real household.db someone has
        been using): Alembic (see alembic/) upgrades it in place. Run
        `alembic upgrade head` before starting the app against real data.
        The Alembic baseline revision (alembic/versions/0001_baseline.py)
        stamps both the pre-freshness-column and post-freshness-column
        shapes cleanly, since both existed in the wild under the old code.
    """
    active_engine = database_engine or engine
    SQLModel.metadata.create_all(active_engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

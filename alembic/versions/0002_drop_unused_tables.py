"""drop tables no code ever read or wrote

A complexity audit found five tables that app/models.py declared and
SQLModel.metadata.create_all therefore created, but which no code path ever
inserted into or selected from:

  google_oauth_states, zepto_oauth_states  -- short-lived OAuth state for
      authorize/callback routes that were never built.
  zepto_connections, google_calendar_connections -- per-household stored
      tokens for those same absent flows.
  demo_store_items -- a demo price list superseded by the commerce mock
      providers, which compute prices rather than reading rows.

All five were verified empty before removal. demo_recipes is deliberately
kept: it is the non-household-scoped model that
tests/test_repositories.py uses to prove Repository.get_for_household
refuses models with no household_id column.

Dropping them also removes the last basis for a claim repeated in
app/api/deps.py and app/repositories.py -- that these rows "hold tokens
capable of placing real paid orders". They held nothing. The auth gate and
the household-scoping rule still stand on pinelabs_connections and budgets,
which are real.

Guarded by an inspector check so this is safe against a database that never
had the tables (a fresh create_all from the current models) as well as one
that did.

Revision ID: 0002_drop_unused_tables
Revises: 0001_baseline
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_drop_unused_tables"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_DEAD_TABLES = (
    "google_oauth_states",
    "zepto_oauth_states",
    "zepto_connections",
    "google_calendar_connections",
    "demo_store_items",
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    for table_name in _DEAD_TABLES:
        if table_name in existing:
            op.drop_table(table_name)


def downgrade() -> None:
    """Deliberately not reversible.

    Recreating empty tables that nothing reads would restore the schema but
    not any behaviour, and there is no data to bring back. If these flows are
    built later, they should come with migrations describing the shape they
    actually need rather than resurrecting a guess made before the routes
    existed.
    """
    raise NotImplementedError("0002_drop_unused_tables is not reversible; the dropped tables held no data.")

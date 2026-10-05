"""record the priced basket on the loop so execute-order stops trusting the client

`POST /households/{id}/loops/{id}/execute-order` used to take `amount_inr` and
`tier` as query parameters. Both fed straight into the approval gate, which
meant the gate was validating numbers the caller had chosen: a request for
`?amount_inr=2000&tier=green` made `check_execution_authorized` return early on
"green tier executes without approval" without ever looking for an approval
row, and the registry's payment gate received a hardcoded
`budget_check_passed: True` alongside it. Anyone who could reach the endpoint
could spend past the gate the README names as one of the two tickets that
matter most.

Closing that means the server has to know the basket's price without being
told. It already almost does -- `_record_approval_if_needed` writes
`amount_inr` onto an `ApprovalRequest` -- but only for tiers that require
approval, so a green-tier loop had the figure nowhere in the database and a
green execute would have had nothing to derive from. These four columns give
every planned loop, at any tier, a server-side record of what was quoted and
when.

`quoted_tier` is stored rather than recomputed at execution time because
re-running the classifier would need the basket, which needs the gap, which
needs the recipe -- and generated recipes are deliberately never persisted.

All four are nullable: a loop that has never been planned genuinely has no
basket, and execute-order refuses that case with a 409 rather than defaulting
to zero and quietly executing a free order.

Guarded by an inspector check, because a fresh database gets these columns
from SQLModel.metadata.create_all via app.database.initialize_database() and
this migration must be a no-op against it.

Revision ID: 0003_loop_quote_snapshot
Revises: 0002_drop_unused_tables
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_loop_quote_snapshot"
down_revision = "0002_drop_unused_tables"
branch_labels = None
depends_on = None

_TABLE = "meal_loop_records"

# Mirrors the SQLModel annotations on MealLoopRecord. quoted_tier is rendered
# as a plain VARCHAR to match what create_all produces for a str-valued Enum
# on SQLite, so a migrated database and a freshly created one agree.
_NEW_COLUMNS = (
    ("quoted_amount_inr", sa.Float(), True),
    ("quoted_tier", sa.String(length=16), True),
    ("quoted_provider", sa.String(length=64), True),
    ("quoted_at", sa.DateTime(), True),
)


def _existing_columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        return
    present = _existing_columns(inspector, _TABLE)
    missing = [(name, type_, nullable) for name, type_, nullable in _NEW_COLUMNS if name not in present]
    if not missing:
        return
    with op.batch_alter_table(_TABLE) as batch:
        for name, type_, nullable in missing:
            batch.add_column(sa.Column(name, type_, nullable=nullable))


def downgrade() -> None:
    """Reversible, unlike 0002.

    These columns are additive and hold only derived data -- the basket can be
    re-quoted by planning the loop again -- so dropping them loses a
    convenience, not a record. Note that reverting the schema without also
    reverting the application code would restore the vulnerability this
    migration exists to close.
    """
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        return
    present = _existing_columns(inspector, _TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        for name, _type, _nullable in _NEW_COLUMNS:
            if name in present:
                batch.drop_column(name)

"""instamart_cart_snapshots: what was put into an Instamart cart for a loop

Guarded by an inspector check, because a fresh database gets this table from
SQLModel.metadata.create_all via app.database.initialize_database() and this
migration must be a no-op against it.

Revision ID: 0004_instamart_cart_snapshots
Revises: 0003_loop_quote_snapshot
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_instamart_cart_snapshots"
down_revision = "0003_loop_quote_snapshot"
branch_labels = None
depends_on = None

_TABLE = "instamart_cart_snapshots"


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if not tables or _TABLE in tables:
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"), nullable=False, index=True),
        sa.Column("meal_loop_id", sa.Integer(), sa.ForeignKey("meal_loop_records.id"), nullable=False, index=True),
        sa.Column("selected_address_id", sa.String(length=128), nullable=False),
        sa.Column("missing_ingredients_json", sa.JSON(), nullable=True),
        sa.Column("matched_items_json", sa.JSON(), nullable=True),
        sa.Column("unmatched_items_json", sa.JSON(), nullable=True),
        sa.Column("live_cart_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    if _TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table(_TABLE)

"""baseline — stamp existing household.db shapes, add build-map columns

Handles three cases seen in the wild for this project:
  1. A brand-new database with no tables at all -> create_all handles it
     via app.database.initialize_database(); this revision has nothing to do
     and simply stamps head.
  2. An existing household.db from BEFORE the original hand-rolled
     `freshness` migration -> add the freshness column.
  3. An existing household.db that already has `freshness` (added by the
     old hand-rolled migration) -> skip it, add only the new build-map
     columns (guest_count, occasion, closure tracking, decision_reason,
     approved_amount_inr) and the new pinelabs_connections table.

Every ADD COLUMN is guarded by an inspector check so this revision is safe
to run against any of the three states without data loss.

Revision ID: 0001_baseline
Revises:
Create Date: (build map remediation sprint)
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _existing_columns(inspector, table_name: str) -> set[str]:
    if table_name not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.get_table_names():
        # Fresh database — SQLModel.metadata.create_all (called from
        # app.database.initialize_database) is responsible for the full
        # schema. Nothing for this revision to do.
        return

    inv_cols = _existing_columns(inspector, "inventory_lots")
    if inv_cols and "freshness" not in inv_cols:
        with op.batch_alter_table("inventory_lots") as batch:
            batch.add_column(sa.Column("freshness", sa.String(32), nullable=False, server_default="fresh"))

    loop_cols = _existing_columns(inspector, "meal_loop_records")
    if loop_cols:
        with op.batch_alter_table("meal_loop_records") as batch:
            if "guest_count" not in loop_cols:
                batch.add_column(sa.Column("guest_count", sa.Integer(), nullable=False, server_default="0"))
            if "occasion" not in loop_cols:
                batch.add_column(sa.Column("occasion", sa.String(120), nullable=True))
            if "cook_confirmed" not in loop_cols:
                batch.add_column(sa.Column("cook_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()))
            if "eater_feedback_captured" not in loop_cols:
                batch.add_column(sa.Column("eater_feedback_captured", sa.Boolean(), nullable=False, server_default=sa.false()))
            if "closed_at" not in loop_cols:
                batch.add_column(sa.Column("closed_at", sa.DateTime(), nullable=True))
            if "unclosed_reason" not in loop_cols:
                batch.add_column(sa.Column("unclosed_reason", sa.String(500), nullable=True))

    approval_cols = _existing_columns(inspector, "approval_requests")
    if approval_cols:
        with op.batch_alter_table("approval_requests") as batch:
            if "decision_reason" not in approval_cols:
                batch.add_column(sa.Column("decision_reason", sa.String(500), nullable=True))
            if "approved_amount_inr" not in approval_cols:
                batch.add_column(sa.Column("approved_amount_inr", sa.Float(), nullable=True))

    if "pinelabs_connections" not in inspector.get_table_names():
        op.create_table(
            "pinelabs_connections",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"), nullable=False, index=True),
            sa.Column("encrypted_mandate_token", sa.String(), nullable=False),
            sa.Column("reserved_ceiling_inr", sa.Float(), nullable=False, server_default="0"),
            sa.Column("ceiling_used_inr", sa.Float(), nullable=False, server_default="0"),
            sa.Column("period_started_on", sa.Date(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("household_id", name="uq_pinelabs_household"),
        )


def downgrade() -> None:
    # Additive-only migration by design; downgrade is intentionally a no-op
    # rather than a destructive column drop against real household data.
    pass

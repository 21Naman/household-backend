"""Persistent Phase 1 fixtures and Phase 2 household-memory entities.

Changed from the original upload per the Build Map:
  - Ticket #2: tier/status columns are now typed Enums, not free strings.
  - Ticket #23: MealLoopRecord gets structured guest_count/occasion instead
    of relying entirely on free-text context_note.
  - Ticket #24: MealLoopRecord gets closed_at/unclosed_reason so the sweep
    can flag a loop as unclosed rather than leaving it silently open.
  - Ticket #26: ApprovalRequest gets a decision_reason, symmetric with the
    existing `reason` (which explains why approval was *asked*, not why it
    was granted or denied).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

UTC = timezone.utc

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.enums import ApprovalStatus, LoopStatus, SpendTier, TaskStatus


class DemoRecipe(SQLModel, table=True):
    __tablename__ = "demo_recipes"
    __table_args__ = (UniqueConstraint("slug", name="uq_demo_recipe_slug"),)
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True)
    name: str
    ingredients: list[dict[str, Any]] = Field(sa_column=Column(JSON), default_factory=list)


class Household(SQLModel, table=True):
    __tablename__ = "households"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, min_length=1, max_length=120)
    default_language: str = Field(default="English", max_length=32)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HouseholdMember(SQLModel, table=True):
    __tablename__ = "household_members"
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    name: str = Field(min_length=1, max_length=120)
    language: str = Field(default="English", max_length=32)
    dietary_preferences: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    allergies: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    health_constraints: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    likes: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    dislikes: list[str] = Field(sa_column=Column(JSON), default_factory=list)


class PineLabsConnection(SQLModel, table=True):
    """Ticket #18/#40. One reserved-ceiling mandate per household, mirroring
    ZeptoConnection's shape. `reserved_ceiling_inr` is the Bible §3.1 P3P
    "block now, debit on event" ceiling; `ceiling_used_inr` tracks the
    running debit against it within the current period."""
    __tablename__ = "pinelabs_connections"
    __table_args__ = (UniqueConstraint("household_id", name="uq_pinelabs_household"),)
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    encrypted_mandate_token: str
    reserved_ceiling_inr: float = Field(default=0, ge=0)
    ceiling_used_inr: float = Field(default=0, ge=0)
    period_started_on: date = Field(default_factory=date.today)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CookProfile(SQLModel, table=True):
    __tablename__ = "cook_profiles"
    __table_args__ = (UniqueConstraint("household_id", name="uq_cook_profile_household"),)
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    name: str = Field(default="Cook", max_length=120)
    language: str = Field(default="Hindi", max_length=32)
    skill_level: str = Field(default="intermediate", max_length=32)
    available_hours: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    confident_dishes: list[str] = Field(sa_column=Column(JSON), default_factory=list)


class InventoryLot(SQLModel, table=True):
    __tablename__ = "inventory_lots"
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    ingredient: str = Field(index=True, min_length=1, max_length=120)
    quantity: float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=32)
    purchased_on: date | None = None
    expiry_date: date | None = Field(default=None, index=True)
    # Raw visual-capture input only. Ticket #9's effective_freshness() in
    # app/services.py is the reconciled, authoritative reading — nothing
    # downstream should read this field directly.
    freshness: str = Field(default="fresh", max_length=32)
    storage_location: str = Field(default="pantry", max_length=32)
    confirmed: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Leftover(SQLModel, table=True):
    __tablename__ = "leftovers"
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    dish_name: str = Field(min_length=1, max_length=120)
    portions: float = Field(ge=0)
    stored_on: date = Field(default_factory=date.today)
    expiry_date: date | None = Field(default=None, index=True)
    storage_location: str = Field(default="fridge", max_length=32)
    reuse_suggestions: list[str] = Field(sa_column=Column(JSON), default_factory=list)


class Dish(SQLModel, table=True):
    __tablename__ = "dishes"
    id: int | None = Field(default=None, primary_key=True)
    household_id: int | None = Field(default=None, foreign_key="households.id", index=True)
    name: str = Field(index=True, min_length=1, max_length=120)
    ingredients: list[dict[str, Any]] = Field(sa_column=Column(JSON), default_factory=list)
    prep_minutes: int = Field(default=0, ge=0)
    servings: int = Field(default=1, ge=1)
    nutrition_notes: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    tags: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    cook_skill_required: str = Field(default="intermediate", max_length=32)


class DishHistory(SQLModel, table=True):
    __tablename__ = "dish_history"
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    dish_id: int | None = Field(default=None, foreign_key="dishes.id", index=True)
    dish_name: str = Field(min_length=1, max_length=120)
    served_on: date = Field(default_factory=date.today, index=True)
    accepted: bool | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    feedback: str | None = Field(default=None, max_length=1000)
    leftovers_portions: float = Field(default=0, ge=0)
    cook_modifications: str | None = Field(default=None, max_length=1000)


class Budget(SQLModel, table=True):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("household_id", name="uq_budget_household"),)
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    monthly_limit: float = Field(ge=0)
    spent_amount: float = Field(default=0, ge=0)
    planned_amount: float = Field(default=0, ge=0)
    category_allocations: dict[str, float] = Field(sa_column=Column(JSON), default_factory=dict)


class PreferenceSignal(SQLModel, table=True):
    """The ONLY channel for transient, short-term preference information
    (Ticket #23). A guest's one-off dislike or a festival occasion must
    never write to HouseholdMember.likes/dislikes — it goes here, with
    expires_on set, per Bible §4.3's long-term vs short-term memory split."""
    __tablename__ = "preference_signals"
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    member_id: int | None = Field(default=None, foreign_key="household_members.id", index=True)
    signal: str = Field(min_length=1, max_length=1000)
    sentiment: str = Field(default="neutral", max_length=32)
    context: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=1.0, ge=0, le=1)
    expires_on: date | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MealLoopRecord(SQLModel, table=True):
    """The six-step loop. Ticket #23 adds structured short-term context;
    Ticket #24 adds the fields the unclosed-loop sweep needs. A loop closes
    ONLY on cook confirmation AND eater feedback (Bible §4.3) — anything
    else at timeout becomes UNCLOSED, never COMPLETED."""
    __tablename__ = "meal_loop_records"
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    trigger_type: str = Field(default="manual", max_length=32)
    context_note: str | None = Field(default=None, max_length=1000)
    status: LoopStatus = Field(default=LoopStatus.RECORDED)
    # Structured short-term context (Ticket #23) — never written back to
    # HouseholdMember; see app/services.py's memory-separation enforcement.
    guest_count: int = Field(default=0, ge=0)
    occasion: str | None = Field(default=None, max_length=120)
    # Closure tracking (Ticket #24)
    cook_confirmed: bool = False
    eater_feedback_captured: bool = False
    closed_at: datetime | None = None
    unclosed_reason: str | None = Field(default=None, max_length=500)
    # The priced basket /plan or /recipe last computed for this loop
    # (Ticket #12, hardened). execute-order derives its amount and tier from
    # here instead of from client query parameters -- those used to be
    # caller-supplied, which meant a client could declare tier=green on a
    # ₹2000 basket and the approval gate would check a number the caller
    # chose. The amount previously survived only on an ApprovalRequest, which
    # is created for non-green tiers alone, so a green loop had it nowhere.
    # Nullable because a loop that has never been planned has no basket;
    # execute-order refuses in that case rather than defaulting to zero.
    quoted_amount_inr: float | None = None
    quoted_tier: SpendTier | None = None
    quoted_provider: str | None = Field(default=None, max_length=64)
    quoted_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalRequest(SQLModel, table=True):
    __tablename__ = "approval_requests"
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    meal_loop_id: int = Field(foreign_key="meal_loop_records.id", index=True)
    tier: SpendTier
    action: str
    amount_inr: float = 0
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    reason: str  # why approval was ASKED (Ticket #11's classifier reason)
    decision_reason: str | None = Field(default=None, max_length=500)  # why it was GRANTED/DENIED (Ticket #26)
    # Snapshot of the basket total at approval time so Ticket #12's
    # execution-time re-check can detect a changed basket under a stale approval.
    approved_amount_inr: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None


class LocalTask(SQLModel, table=True):
    __tablename__ = "local_tasks"
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    meal_loop_id: int = Field(foreign_key="meal_loop_records.id", index=True)
    task_type: str
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    details: str


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    meal_loop_id: int | None = Field(default=None, foreign_key="meal_loop_records.id", index=True)
    event: str
    detail: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

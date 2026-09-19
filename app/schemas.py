from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.enums import LoopStatus, ProcurementPath, SpendTier


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HouseholdCreate(APIModel):
    name: str = Field(min_length=1, max_length=120)
    default_language: str = "English"


class HouseholdUpdate(HouseholdCreate):
    pass


class MemberCreate(APIModel):
    name: str = Field(min_length=1, max_length=120)
    language: str = "English"
    dietary_preferences: list[str] = []
    allergies: list[str] = []
    health_constraints: list[str] = []
    likes: list[str] = []
    dislikes: list[str] = []


class CookProfileWrite(APIModel):
    name: str = "Cook"
    language: str = "Hindi"
    skill_level: str = "intermediate"
    available_hours: list[str] = []
    confident_dishes: list[str] = []


class InventoryCreate(APIModel):
    ingredient: str = Field(min_length=1, max_length=120)
    quantity: float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=32)
    purchased_on: date | None = None
    expiry_date: date | None = None
    freshness: str = "fresh"
    storage_location: str = "pantry"
    confirmed: bool = False


class InventoryUpdate(InventoryCreate):
    pass


class LeftoverCreate(APIModel):
    dish_name: str = Field(min_length=1, max_length=120)
    portions: float = Field(ge=0)
    stored_on: date = Field(default_factory=date.today)
    expiry_date: date | None = None
    storage_location: str = "fridge"
    reuse_suggestions: list[str] = []


class DishCreate(APIModel):
    name: str = Field(min_length=1, max_length=120)
    ingredients: list[dict[str, Any]] = []
    prep_minutes: int = Field(default=0, ge=0)
    servings: int = Field(default=1, ge=1)
    nutrition_notes: list[str] = []
    tags: list[str] = []
    cook_skill_required: str = "intermediate"


# -- Typed commerce payloads (Ticket #20) ------------------------------------

class CartItem(APIModel):
    product_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=32)
    unit_price_inr: float = Field(ge=0)


class HistoryCreate(APIModel):
    dish_id: int | None = None
    dish_name: str = Field(min_length=1, max_length=120)
    served_on: date = Field(default_factory=date.today)
    accepted: bool | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    feedback: str | None = None
    leftovers_portions: float = Field(default=0, ge=0)
    cook_modifications: str | None = None


class BudgetWrite(APIModel):
    monthly_limit: float = Field(ge=0)
    spent_amount: float = Field(default=0, ge=0)
    planned_amount: float = Field(default=0, ge=0)
    category_allocations: dict[str, float] = {}


class PreferenceCreate(APIModel):
    member_id: int | None = None
    signal: str = Field(min_length=1, max_length=1000)
    sentiment: str = "neutral"
    context: str | None = None
    confidence: float = Field(default=1, ge=0, le=1)
    expires_on: date | None = None


class PlanRequest(APIModel):
    servings: int = Field(default=2, ge=1, le=20)
    available_minutes: int = Field(default=45, ge=1, le=360)
    guests: int = Field(default=0, ge=0, le=18)
    urgency: str = "routine"


class RecipeGenerationRequest(PlanRequest):
    """Inputs shared with deterministic planning, kept as a separate public
    model so the versioned recipe endpoint can evolve independently."""


class RecipeIngredient(APIModel):
    model_config = ConfigDict(extra="forbid")

    ingredient: str = Field(min_length=1, max_length=120)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=32)

    @field_validator("ingredient", "unit")
    @classmethod
    def reject_blank_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ingredient and unit must not be blank")
        return value


class GeneratedRecipe(APIModel):
    """The constrained, response-only LLM proposal.

    `ingredients` contains the measurable/purchasable recipe ingredients.
    Everyday basics the model assumes are represented separately and do not
    enter the shopping-gap calculation.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    servings: int = Field(ge=1, le=20)
    prep_minutes: int = Field(ge=1, le=360)
    ingredients: list[RecipeIngredient] = Field(min_length=1, max_length=30)
    assumed_pantry_staples: list[str] = Field(max_length=20)
    steps: list[str] = Field(min_length=1, max_length=15)
    nutrition_notes: list[str] = Field(min_length=1, max_length=10)

    @field_validator("title")
    @classmethod
    def reject_blank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("recipe title must not be blank")
        return value

    @field_validator("assumed_pantry_staples", "steps", "nutrition_notes")
    @classmethod
    def reject_blank_text(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("recipe text entries must not be blank")
        return value

    @model_validator(mode="after")
    def reject_duplicate_ingredients(self):
        names = [item.ingredient.strip().casefold() for item in self.ingredients]
        if len(names) != len(set(names)):
            raise ValueError("recipe ingredients must not contain duplicates")
        return self


class RecipeQuote(APIModel):
    provider_name: str
    total_inr: float = Field(ge=0)
    feasible: bool


class RecipeGenerationResponse(APIModel):
    recipe: GeneratedRecipe
    # The shopping list: what the recipe needs that the household does not
    # already have. There is deliberately only one field for this.
    missing_ingredients: list["GapItem"]
    availability_ratio: float = Field(ge=0, le=1)
    quotes: list[RecipeQuote]
    procurement_path: ProcurementPath
    procurement_reason: str
    chosen_provider: str | None = None
    amount_inr: float = Field(ge=0)
    tier: SpendTier
    tier_reason: str
    approval_request_id: int | None = None
    delivery_confidence: float = Field(ge=0, le=1)
    generation_provider: str


class LoopStart(APIModel):
    trigger_type: str = "manual"
    context_note: str | None = None
    guest_count: int = Field(default=0, ge=0)
    occasion: str | None = Field(default=None, max_length=120)


class TransitionRequest(APIModel):
    # Ticket #2: was an unconstrained `str`; a typo used to silently write a
    # corrupted loop record. UNCLOSED is deliberately excluded from the
    # values a client may POST — it is written only by Ticket #24's sweep.
    target_status: LoopStatus

    def validate_not_system_only(self) -> None:
        if self.target_status == LoopStatus.UNCLOSED:
            raise ValueError("UNCLOSED may only be set by the unclosed-loop sweep, not via the API.")


class ApprovalDecision(APIModel):
    approved: bool
    # Ticket #26 — symmetric with ApprovalRequest.reason (why approval was
    # ASKED). This is why it was GRANTED or DENIED, feeding the weekly
    # reflection mechanism (Ticket #36).
    reason: str | None = Field(default=None, max_length=500)


class OutcomeCapture(APIModel):
    dish_name: str
    rating: int | None = Field(default=None, ge=1, le=5)
    feedback: str = ""
    leftovers_portions: float = Field(default=0, ge=0)
    consumed: list[CartItem] = []
    cook_status: str = "confirmed"
    mishap: bool = False


class CaptureCandidate(APIModel):
    ingredient: str
    quantity: float = Field(ge=0)
    unit: str
    expiry_date: date | None = None
    freshness: str = "fresh"
    storage_location: str = "pantry"
    readability_confidence: float = Field(ge=0, le=1)


class CapturePreview(APIModel):
    source: str
    transcript: str | None = None
    language: str | None = None
    candidates: list[CaptureCandidate] = []
    requires_confirmation: bool = True
    fallback: str | None = None
    warning: str | None = None


# -- Response models for the deterministic services (Phase 1) ---------------

class GapItem(APIModel):
    ingredient: str
    missing_quantity: float
    unit: str
    quantity_unknown: bool = False


# RecipeGenerationResponse is declared before GapItem so the public recipe
# models stay adjacent to PlanRequest. Resolve that forward reference once all
# response models have been defined.
RecipeGenerationResponse.model_rebuild()

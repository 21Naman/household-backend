"""Plain household-scoped CRUD.

Split out of app/api/routes.py, which had grown to hold both these ~20
two-line handlers and the loop orchestration logic. Nothing here makes a
decision: every handler is a thin, household-scoped pass-through to
app.repositories.Repository. The one exception is the photo-capture
endpoint, which reads a fridge photo through the vision provider and always
returns requires_confirmation=True so a human confirms before anything is
written to InventoryLot.

Decision logic -- planning, pricing, tiering, approval, execution -- lives
in app/api/routes.py. Both routers mount under the same /api prefix.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlmodel import Session, select

from app.api.deps import get_db_session, require_api_key
from app.models import (
    Budget,
    CookProfile,
    Dish,
    DishHistory,
    Household,
    HouseholdMember,
    InventoryLot,
    Leftover,
    PreferenceSignal,
)
from app.repositories import Repository
from app.schemas import (
    BudgetWrite,
    CaptureCandidate,
    CapturePreview,
    CookProfileWrite,
    DishCreate,
    HistoryCreate,
    HouseholdCreate,
    HouseholdUpdate,
    InventoryCreate,
    InventoryUpdate,
    LeftoverCreate,
    MemberCreate,
    PreferenceCreate,
)
from app.settings import Settings, get_settings

router = APIRouter(dependencies=[Depends(require_api_key)])


# ============================================================================
# Households, members, cook profile -- plain CRUD over Repository
# ============================================================================

@router.post("/households", status_code=201)
def create_household(payload: HouseholdCreate, session: Session = Depends(get_db_session)) -> Household:
    repo = Repository(Household, session)
    return repo.create(Household(**payload.model_dump()))


@router.get("/households/{household_id}")
def get_household(household_id: int, session: Session = Depends(get_db_session)) -> Household:
    return Repository(Household, session).get(household_id)


@router.patch("/households/{household_id}")
def update_household(household_id: int, payload: HouseholdUpdate, session: Session = Depends(get_db_session)) -> Household:
    repo = Repository(Household, session)
    return repo.update(repo.get(household_id), payload.model_dump())


@router.post("/households/{household_id}/members", status_code=201)
def create_member(household_id: int, payload: MemberCreate, session: Session = Depends(get_db_session)) -> HouseholdMember:
    repo = Repository(HouseholdMember, session)
    return repo.create(HouseholdMember(household_id=household_id, **payload.model_dump()))


@router.get("/households/{household_id}/members")
def list_members(household_id: int, session: Session = Depends(get_db_session)) -> list[HouseholdMember]:
    return Repository(HouseholdMember, session).list_for_household(household_id)


@router.put("/households/{household_id}/cook-profile")
def upsert_cook_profile(household_id: int, payload: CookProfileWrite, session: Session = Depends(get_db_session)) -> CookProfile:
    repo = Repository(CookProfile, session)
    existing = repo.list_for_household(household_id)
    if existing:
        return repo.update(existing[0], payload.model_dump())
    return repo.create(CookProfile(household_id=household_id, **payload.model_dump()))


@router.get("/households/{household_id}/cook-profile")
def get_cook_profile(household_id: int, session: Session = Depends(get_db_session)) -> CookProfile:
    rows = Repository(CookProfile, session).list_for_household(household_id)
    if not rows:
        raise HTTPException(404, "No cook profile set for this household")
    return rows[0]


# ============================================================================
# Inventory
# ============================================================================

@router.post("/households/{household_id}/inventory", status_code=201)
def create_inventory_lot(household_id: int, payload: InventoryCreate, session: Session = Depends(get_db_session)) -> InventoryLot:
    repo = Repository(InventoryLot, session)
    return repo.create(InventoryLot(household_id=household_id, **payload.model_dump()))


@router.get("/households/{household_id}/inventory")
def list_inventory(household_id: int, session: Session = Depends(get_db_session)) -> list[InventoryLot]:
    return Repository(InventoryLot, session).list_for_household(household_id)


@router.patch("/households/{household_id}/inventory/{lot_id}")
def update_inventory_lot(household_id: int, lot_id: int, payload: InventoryUpdate, session: Session = Depends(get_db_session)) -> InventoryLot:
    repo = Repository(InventoryLot, session)
    return repo.update_for_household(lot_id, household_id, payload.model_dump())


@router.delete("/households/{household_id}/inventory/{lot_id}", status_code=204)
def delete_inventory_lot(household_id: int, lot_id: int, session: Session = Depends(get_db_session)) -> None:
    Repository(InventoryLot, session).delete_for_household(lot_id, household_id)


@router.post("/households/{household_id}/inventory/capture/photo", response_model=CapturePreview)
def capture_inventory_from_photo(
    household_id: int, file: UploadFile, settings: Settings = Depends(get_settings)
) -> CapturePreview:
    """Ticket #22's checkpoint beat 1: a fridge photo becomes confirmed
    inventory. requires_confirmation is always True (schemas.CapturePreview)
    so a human confirms before anything is written to InventoryLot."""
    from app.providers.vision import VisionProvider

    vision = VisionProvider(settings.ollama_base_url, settings.vision_model, settings.vision_request_timeout_seconds)
    try:
        image_bytes = file.file.read()
        result = vision.extract_inventory(image_bytes)
    except (RuntimeError, ValueError) as exc:
        return CapturePreview(
            source="photo",
            candidates=[],
            fallback="use typed inventory entry",
            warning=str(exc),
        )
    candidates = [
        CaptureCandidate(
            ingredient=item["ingredient"],
            quantity=item["estimated_quantity"],
            unit=item["unit"],
            expiry_date=item.get("expiry_date"),
            freshness=item.get("freshness", "fresh"),
            storage_location=item.get("storage_hint", "pantry"),
            readability_confidence=item.get("readability_confidence", 0.5),
        )
        for item in result.get("items", [])
    ]
    return CapturePreview(source="photo", candidates=candidates)


@router.post("/households/{household_id}/inventory/capture/confirm", status_code=201)
def confirm_inventory_capture(household_id: int, payload: InventoryCreate, session: Session = Depends(get_db_session)) -> InventoryLot:
    repo = Repository(InventoryLot, session)
    data = payload.model_dump()
    data["confirmed"] = True
    return repo.create(InventoryLot(household_id=household_id, **data))


# ============================================================================
# Leftovers, dishes, history, budget, preferences
# ============================================================================

@router.post("/households/{household_id}/leftovers", status_code=201)
def create_leftover(household_id: int, payload: LeftoverCreate, session: Session = Depends(get_db_session)) -> Leftover:
    return Repository(Leftover, session).create(Leftover(household_id=household_id, **payload.model_dump()))


@router.get("/households/{household_id}/leftovers")
def list_leftovers(household_id: int, session: Session = Depends(get_db_session)) -> list[Leftover]:
    return Repository(Leftover, session).list_for_household(household_id)


@router.post("/dishes", status_code=201)
def create_dish(payload: DishCreate, session: Session = Depends(get_db_session)) -> Dish:
    return Repository(Dish, session).create(Dish(**payload.model_dump()))


@router.get("/dishes")
def list_dishes(session: Session = Depends(get_db_session)) -> list[Dish]:
    return list(session.exec(select(Dish)))


@router.post("/households/{household_id}/history", status_code=201)
def create_history(household_id: int, payload: HistoryCreate, session: Session = Depends(get_db_session)) -> DishHistory:
    return Repository(DishHistory, session).create(DishHistory(household_id=household_id, **payload.model_dump()))


@router.get("/households/{household_id}/history")
def list_history(household_id: int, session: Session = Depends(get_db_session)) -> list[DishHistory]:
    return Repository(DishHistory, session).list_for_household(household_id)


@router.put("/households/{household_id}/budget")
def upsert_budget(household_id: int, payload: BudgetWrite, session: Session = Depends(get_db_session)) -> Budget:
    repo = Repository(Budget, session)
    existing = repo.list_for_household(household_id)
    if existing:
        return repo.update(existing[0], payload.model_dump())
    return repo.create(Budget(household_id=household_id, **payload.model_dump()))


@router.get("/households/{household_id}/budget")
def get_budget(household_id: int, session: Session = Depends(get_db_session)) -> Budget:
    rows = Repository(Budget, session).list_for_household(household_id)
    if not rows:
        raise HTTPException(404, "No budget set for this household")
    return rows[0]


@router.post("/households/{household_id}/preferences", status_code=201)
def create_preference_signal(household_id: int, payload: PreferenceCreate, session: Session = Depends(get_db_session)) -> PreferenceSignal:
    """Ticket #23's ONLY channel for transient preference info. Never
    writes to HouseholdMember.likes/dislikes."""
    return Repository(PreferenceSignal, session).create(PreferenceSignal(household_id=household_id, **payload.model_dump()))


@router.get("/households/{household_id}/preferences")
def list_preference_signals(household_id: int, session: Session = Depends(get_db_session)) -> list[PreferenceSignal]:
    return Repository(PreferenceSignal, session).list_for_household(household_id)

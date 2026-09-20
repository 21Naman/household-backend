"""Loop orchestration: planning, pricing, tiering, approval, execution.

Built fresh per Ticket #1's audit finding: this file did not exist in the
original upload, so the audit's four guarantees were unverifiable. They are
enforced here as follows:

  1. Tier classification is deterministic (app.services.classify_order_tier,
     imported and called directly -- never through the ModelProvider).
  2. Budget/price math is deterministic (app.services.consolidate_orders).
  3. Red-tier actions require a recorded approval, re-checked at EXECUTION
     time (app.services.check_execution_authorized), not just proposal time.
  4. Tool calls go only through app.core.registry.ToolRegistry.invoke,
     which gates on the deterministic tier/context -- routes never call a
     provider directly.

The plain household-scoped CRUD handlers live in app/api/routes_crud.py.
Both routers mount under the same /api prefix in app/main.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, select

from app.api.deps import get_db_session, require_api_key
from app.core.container import Container, get_container
from app.core.registry import ToolCallRefused, ToolKind
from app.core.recipe_audio_cache import RecipeAudioEntry
from app.core.recipe_briefing import RecipeBriefer, RecipeBriefingError
from app.core.recipe_planner import RecipeConstraintError, RecipeGenerator
from app.core.state_store import LocalStateStore
from app.enums import ApprovalStatus, LoopStatus, SpendTier
from app.models import (
    ApprovalRequest,
    AuditEvent,
    Budget,
    CookProfile,
    Dish,
    DishHistory,
    InventoryLot,
    MealLoopRecord,
    PineLabsConnection,
)
from app.providers.language import resolve_voiced_language
from app.providers.model_failures import RecipeProviderOperationalError
from app.providers.voice import VoiceSynthesisUnavailable, join_audio
from app.repositories import Repository
from app.schemas import (
    ApprovalDecision,
    LoopStart,
    OutcomeCapture,
    PlanRequest,
    RecipeGenerationRequest,
    RecipeGenerationResponse,
    RecipeQuote,
    TransitionRequest,
)
from app.services import (
    ProviderQuote,
    approval_required,
    check_execution_authorized,
    classify_order_tier,
    compute_ingredient_gap,
    consolidate_orders,
    remaining_budget,
)

UTC = timezone.utc
logger = logging.getLogger("household_agent")

router = APIRouter(dependencies=[Depends(require_api_key)])


# ============================================================================
# Shared planning helpers -- used by BOTH the deterministic /plan endpoint and
# the v2 /recipe endpoint, so the two paths cannot drift apart on pricing,
# delivery scoring, tier thresholds, or when approval is demanded.
# ============================================================================

def _quote_gap(container: Container, gap) -> list[ProviderQuote]:
    """Price a missing-ingredient basket with every configured provider.

    One provider being down must not skip the other -- a partial comparison
    is still a comparison, and an empty quote list is handled downstream as a
    deterministic feasibility failure rather than as a free basket.
    """
    if not gap:
        return []
    items = [{"ingredient": item.ingredient, "quantity": item.missing_quantity, "unit": item.unit} for item in gap]
    quotes: list[ProviderQuote] = []
    for provider_call in (
        lambda: container.zepto.quote_cart("mock-token", items),
        lambda: container.commerce_second.quote_cart(items),
    ):
        try:
            quote = provider_call()
        except Exception:
            logger.warning("A commerce provider failed to quote the basket; continuing with the others", exc_info=True)
            continue
        quotes.append(ProviderQuote(quote.provider_name, quote.total_inr, feasible=True))
    return quotes


def _score_delivery(container: Container):
    """Delivery confidence against the cook's deadline, from one definition
    of "how long until the meal" rather than a literal at each call site."""
    now = datetime.now(UTC)
    return container.logistics.delivery_confidence(
        address="household",
        order_time=now,
        deadline=now + timedelta(minutes=container.settings.delivery_deadline_minutes),
    )


def _classify_tier(container: Container, amount_inr: float, budget: Budget | None, *, is_routine: bool):
    """classify_order_tier with the configured thresholds applied. Never call
    the classifier with inline defaults -- the thresholds are settings."""
    return classify_order_tier(
        amount_inr,
        budget,
        is_routine=is_routine,
        green_ceiling_inr=container.settings.spend_tier_green_ceiling_inr,
        red_floor_inr=container.settings.spend_tier_red_floor_inr,
        unusual_multiplier=container.settings.spend_tier_unusual_multiplier,
    )


def _record_approval_if_needed(
    session: Session,
    loop_repo: Repository,
    loop: MealLoopRecord,
    *,
    household_id: int,
    loop_id: int,
    tier: SpendTier,
    amount_inr: float,
    action: str,
    reason: str,
) -> ApprovalRequest | None:
    """Ticket #12: anything above green with real money attached parks the
    loop in AWAITING_APPROVAL and records what is being asked for. Returns
    the created request, or None when the order may proceed unattended."""
    if not approval_required(tier) or amount_inr <= 0:
        return None
    approval_request = Repository(ApprovalRequest, session).create(
        ApprovalRequest(
            household_id=household_id,
            meal_loop_id=loop_id,
            tier=tier,
            action=action,
            amount_inr=amount_inr,
            reason=reason,
            status=ApprovalStatus.PENDING,
        )
    )
    loop_repo.update(loop, {"status": LoopStatus.AWAITING_APPROVAL})
    return approval_request


# ============================================================================
# The six-step loop
# ============================================================================

@router.post("/households/{household_id}/loops", status_code=201)
def start_loop(household_id: int, payload: LoopStart, session: Session = Depends(get_db_session)) -> MealLoopRecord:
    """Ticket #23: guest_count/occasion are structured fields on the loop,
    never written back to HouseholdMember."""
    loop = MealLoopRecord(
        household_id=household_id,
        trigger_type=payload.trigger_type,
        context_note=payload.context_note,
        guest_count=payload.guest_count,
        occasion=payload.occasion,
        status=LoopStatus.TRIGGERED,
    )
    return Repository(MealLoopRecord, session).create(loop)


@router.get("/households/{household_id}/loops/{loop_id}")
def get_loop(household_id: int, loop_id: int, session: Session = Depends(get_db_session)) -> MealLoopRecord:
    return Repository(MealLoopRecord, session).get_for_household(loop_id, household_id)


@router.post("/households/{household_id}/loops/{loop_id}/transition")
def transition_loop(household_id: int, loop_id: int, payload: TransitionRequest, session: Session = Depends(get_db_session)) -> MealLoopRecord:
    payload.validate_not_system_only()
    repo = Repository(MealLoopRecord, session)
    loop = repo.get_for_household(loop_id, household_id)
    return repo.update(loop, {"status": payload.target_status})


@router.post("/households/{household_id}/loops/{loop_id}/plan")
def plan_loop(household_id: int, loop_id: int, payload: PlanRequest, session: Session = Depends(get_db_session)):
    """Tickets #10 + #21 wired end to end: pick a dish that fits
    available_minutes, compute the ingredient gap, quote both commerce
    providers, and choose a procurement path deterministically. No model
    call anywhere in this handler -- see app.services's import-boundary
    test."""
    loop_repo = Repository(MealLoopRecord, session)
    loop = loop_repo.get_for_household(loop_id, household_id)

    dish_rows = list(
        session.exec(select(Dish).where((Dish.household_id == household_id) | (Dish.household_id.is_(None))))
    )
    dish_rows = [d for d in dish_rows if d.prep_minutes <= payload.available_minutes]
    if not dish_rows:
        raise HTTPException(422, "No dish fits the available time; widen available_minutes or add dishes")
    dish = dish_rows[0]

    inventory = Repository(InventoryLot, session).list_for_household(household_id)
    servings = payload.servings + payload.guests
    gap = compute_ingredient_gap(dish, inventory, servings)

    container = get_container()
    quotes = _quote_gap(container, gap)

    budget_rows = Repository(Budget, session).list_for_household(household_id)
    budget = budget_rows[0] if budget_rows else None

    delivery = _score_delivery(container)
    path, path_reason, chosen_quote = consolidate_orders(
        gap,
        quotes,
        budget,
        delivery.confidence,
        confidence_threshold=container.settings.delivery_confidence_threshold,
    )

    amount = chosen_quote.total_inr if chosen_quote else 0.0
    is_routine = bool(dish.tags and "routine" in dish.tags) or not gap
    tier, tier_reason = _classify_tier(container, amount, budget, is_routine=is_routine)

    loop_repo.update(loop, {"status": LoopStatus.PLANNED})
    approval_request = _record_approval_if_needed(
        session,
        loop_repo,
        loop,
        household_id=household_id,
        loop_id=loop_id,
        tier=tier,
        amount_inr=amount,
        action=f"order for {dish.name}",
        reason=tier_reason,
    )

    session.add(
        AuditEvent(
            household_id=household_id,
            meal_loop_id=loop_id,
            event="plan_computed",
            detail=f"dish={dish.name} tier={tier.value} path={path.value} amount={amount:.2f} reason={tier_reason}; {path_reason}",
        )
    )
    session.commit()

    return {
        "dish": {"id": dish.id, "name": dish.name},
        "gap": [g.__dict__ for g in gap],
        "tier": tier.value,
        "tier_reason": tier_reason,
        "procurement_path": path.value,
        "procurement_reason": path_reason,
        "chosen_provider": chosen_quote.provider_name if chosen_quote else None,
        "amount_inr": amount,
        "approval_request_id": approval_request.id if approval_request else None,
        "delivery_confidence": delivery.confidence,
    }


@router.post(
    "/v2/households/{household_id}/loops/{loop_id}/recipe",
    response_model=RecipeGenerationResponse,
)
def generate_recipe(
    household_id: int,
    loop_id: int,
    payload: RecipeGenerationRequest,
    session: Session = Depends(get_db_session),
) -> RecipeGenerationResponse:
    """Generate one fresh recipe, then validate and price it deterministically.

    Only an operational model-provider error causes provider failover. A
    proposal that is over budget, too slow, or poorly stocked receives one
    correction request before this endpoint returns 422 unchanged.
    """
    loop_repo = Repository(MealLoopRecord, session)
    loop = loop_repo.get_for_household(loop_id, household_id)
    state = LocalStateStore(session).get_household_state(household_id)
    container = get_container()

    # A client-provided guest count is explicit for this planning call. When
    # omitted (zero), retain the structured guests captured when the loop was
    # started. Servings are the household servings plus these guests, matching
    # the established deterministic /plan endpoint.
    guests = payload.guests if payload.guests else loop.guest_count
    servings = payload.servings + guests
    generator = RecipeGenerator(
        container.recipe_model_provider,
        inventory_recency_window_hours=container.settings.inventory_recency_window_hours,
        min_stocked_ingredient_ratio=container.settings.recipe_min_stocked_ingredient_ratio,
    )
    prompt = generator.build_prompt(state, loop, payload, servings=servings, guests=guests)
    correction_provider: str | None = None
    last_violations: list[str] = []

    assessment = None
    quotes: list[ProviderQuote] = []
    budget = state.budget
    for attempt in range(2):
        request_prompt = prompt if attempt == 0 else generator.build_correction_prompt(prompt, last_violations)
        try:
            recipe = generator.generate(request_prompt, correction_provider=correction_provider)
            correction_provider = generator.last_provider_name
            # RoundRobinRecipeProvider.last_provider_name is a shared mutable
            # field on a process-wide singleton. The briefing rewrite calls
            # the same chain from the audio endpoint, so reading it later --
            # after any await or across a concurrent request -- can report
            # the wrong author. Capture it here, next to the call it
            # describes.
            generation_provider = generator.last_provider_name or "unknown"
            assessment = generator.assess(
                recipe,
                state.inventory,
                servings=servings,
                available_minutes=payload.available_minutes,
            )
            quotes = _quote_gap(container, assessment.gap)
            if assessment.gap:
                feasible = [quote for quote in quotes if quote.feasible]
                if not feasible:
                    raise RecipeConstraintError(["no commerce provider could quote the complete missing-ingredient basket"])
                cheapest = min(feasible, key=lambda quote: quote.total_inr)
                remaining = remaining_budget(budget)
                if remaining is not None and cheapest.total_inr > remaining:
                    raise RecipeConstraintError(
                        [
                            f"the cheapest missing-ingredient basket costs ₹{cheapest.total_inr:.2f}, "
                            f"above the remaining budget of ₹{remaining:.2f}"
                        ]
                    )
            break
        except RecipeConstraintError as exc:
            last_violations = exc.violations
            if attempt == 1:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    {"message": "No feasible generated recipe was found", "violations": last_violations},
                ) from exc
        except RecipeProviderOperationalError as exc:
            # The chain has already tried the alternate remote and local
            # Ollama fallback. Do not alter loop state on a failed generation.
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    if assessment is None:
        # This branch is defensive: provider-chain failures are raised rather
        # than silently turning into a plan, and every semantic failure above
        # exits with 422 after its one permitted correction.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No recipe-generation provider is available")

    delivery = _score_delivery(container)
    path, path_reason, chosen_quote = consolidate_orders(
        assessment.gap,
        quotes,
        budget,
        delivery.confidence,
        confidence_threshold=container.settings.delivery_confidence_threshold,
    )
    amount = chosen_quote.total_inr if chosen_quote else 0.0
    is_routine = payload.urgency.strip().lower() == "routine" or not assessment.gap
    tier, tier_reason = _classify_tier(container, amount, budget, is_routine=is_routine)

    # Nothing is written until both model and deterministic feasibility checks
    # have succeeded. Generated recipe text and the raw prompt remain in RAM.
    loop_repo.update(loop, {"status": LoopStatus.PLANNED})
    approval_request = _record_approval_if_needed(
        session,
        loop_repo,
        loop,
        household_id=household_id,
        loop_id=loop_id,
        tier=tier,
        amount_inr=amount,
        action=f"order for {assessment.recipe.title}",
        reason=tier_reason,
    )

    provider_name = generation_provider
    session.add(
        AuditEvent(
            household_id=household_id,
            meal_loop_id=loop_id,
            event="recipe_generated",
            detail=(
                f"provider={provider_name} tier={tier.value} path={path.value} "
                f"amount={amount:.2f} availability={assessment.availability_ratio:.2f}"
            ),
        )
    )
    session.commit()

    missing_payload = [
        {
            "ingredient": item.ingredient,
            "missing_quantity": item.missing_quantity,
            "unit": item.unit,
            "quantity_unknown": item.quantity_unknown,
        }
        for item in assessment.gap
    ]
    audio_id = _stage_recipe_audio(
        container, session, household_id, loop_id, assessment.recipe, missing_payload
    )

    return RecipeGenerationResponse(
        recipe=assessment.recipe,
        missing_ingredients=missing_payload,
        availability_ratio=assessment.availability_ratio,
        quotes=[RecipeQuote(provider_name=q.provider_name, total_inr=q.total_inr, feasible=q.feasible) for q in quotes],
        procurement_path=path,
        procurement_reason=path_reason,
        chosen_provider=chosen_quote.provider_name if chosen_quote else None,
        amount_inr=amount,
        tier=tier,
        tier_reason=tier_reason,
        approval_request_id=approval_request.id if approval_request else None,
        delivery_confidence=delivery.confidence,
        generation_provider=provider_name,
        audio_id=audio_id,
    )


@router.post("/households/{household_id}/approvals/{approval_id}/decide")
def decide_approval(household_id: int, approval_id: int, payload: ApprovalDecision, session: Session = Depends(get_db_session)) -> ApprovalRequest:
    """Ticket #12 + #26. Records the decision AND a snapshot amount so
    Ticket #12's execution-time re-check can detect a changed basket."""
    repo = Repository(ApprovalRequest, session)
    approval = repo.get_for_household(approval_id, household_id)
    new_status = ApprovalStatus.APPROVED if payload.approved else ApprovalStatus.REJECTED
    updated = repo.update(
        approval,
        {
            "status": new_status,
            "decision_reason": payload.reason,
            "decided_at": datetime.now(UTC),
            "approved_amount_inr": approval.amount_inr if payload.approved else None,
        },
    )
    session.add(
        AuditEvent(
            household_id=household_id,
            meal_loop_id=approval.meal_loop_id,
            event="approval_decided",
            detail=f"approved={payload.approved} reason={payload.reason or ''}",
        )
    )
    session.commit()
    return updated


@router.post("/households/{household_id}/loops/{loop_id}/execute-order")
def execute_order(household_id: int, loop_id: int, amount_inr: float, tier: SpendTier, session: Session = Depends(get_db_session)):
    """Ticket #12's execution-time gate, wired to Ticket #18's mock
    payment authorizer through the registry (Ticket #5) -- never called
    directly. This is the endpoint the stale-approval test exercises."""
    approval = None
    if tier != SpendTier.GREEN:
        rows = list(
            session.exec(
                select(ApprovalRequest)
                .where(ApprovalRequest.meal_loop_id == loop_id)
                .where(ApprovalRequest.household_id == household_id)
                .order_by(ApprovalRequest.created_at.desc())
            )
        )
        approval = rows[0] if rows else None

    authorized, reason = check_execution_authorized(approval, tier, amount_inr)
    if not authorized:
        session.add(AuditEvent(household_id=household_id, meal_loop_id=loop_id, event="execution_refused", detail=reason))
        session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)

    container = get_container()
    pinelabs_rows = list(session.exec(select(PineLabsConnection).where(PineLabsConnection.household_id == household_id)))
    connection = pinelabs_rows[0] if pinelabs_rows else None

    try:
        result = container.registry.invoke(
            ToolKind.PAYMENTS,
            "authorize",
            {"tier": tier, "budget_check_passed": True},
            connection,
            amount_inr,
            tier,
        )
    except ToolCallRefused as exc:
        session.add(AuditEvent(household_id=household_id, meal_loop_id=loop_id, event="tool_call_refused", detail=str(exc)))
        session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    if connection is not None:
        session.add(connection)
        session.commit()

    session.add(
        AuditEvent(
            household_id=household_id,
            meal_loop_id=loop_id,
            event="order_executed",
            detail=f"executed={result.executed} reference={result.reference} reason={result.reason}",
        )
    )
    session.commit()
    return {"executed": result.executed, "requires_human": result.requires_human, "reference": result.reference, "reason": result.reason}


@router.post("/households/{household_id}/loops/{loop_id}/cook-brief")
def send_cook_brief(household_id: int, loop_id: int, dish_name: str, instructions: str, session: Session = Depends(get_db_session)):
    """Ticket #25 wired through the registry -- the reply leg."""
    cook_rows = Repository(CookProfile, session).list_for_household(household_id)
    cook = cook_rows[0] if cook_rows else CookProfile(household_id=household_id)

    container = get_container()
    try:
        brief = container.registry.invoke(
            ToolKind.VOICE,
            "reply",
            {"cook_facing": True},
            dish_name=dish_name,
            instructions=instructions,
            language=cook.language,
            skill_level=cook.skill_level,
        )
    except ToolCallRefused as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    session.add(AuditEvent(household_id=household_id, meal_loop_id=loop_id, event="cook_brief_sent", detail=f"language={brief.language}"))
    session.commit()
    return {"text": brief.text, "language": brief.language}


def _stage_recipe_audio(container, session, household_id, loop_id, recipe, missing_payload) -> str | None:
    """Park the recipe so it can be voiced later, and return its handle.

    No model call and no synthesis happen here -- that is the whole point of
    doing the work at fetch time.

    The cook's language is resolved now, not at fetch, and the resolved
    language and voice are carried on the entry. Every household gets audio:
    a language with its own configured voice keeps it, and everything else
    falls back to the configured default (Hinglish), which is a briefing an
    Indian home cook can actually follow rather than a guessed language.

    Returns None only if the fallback itself is misconfigured, which is an
    operator error rather than a property of this household.
    """
    cook_rows = Repository(CookProfile, session).list_for_household(household_id)
    cook = cook_rows[0] if cook_rows else CookProfile(household_id=household_id)
    settings = container.settings
    try:
        language, voice = resolve_voiced_language(
            cook.language,
            settings.gnani_language_map,
            settings.gnani_voice_map,
            settings.gnani_default_language,
        )
    except ValueError as exc:
        logger.warning("Audio briefing unavailable: %s", exc)
        return None
    return container.recipe_audio_cache.put(
        RecipeAudioEntry(
            household_id=household_id,
            loop_id=loop_id,
            recipe=recipe,
            missing_ingredients=missing_payload,
            language=language,
            voice=voice,
            skill_level=cook.skill_level,
            created_at=datetime.now(timezone.utc),
        )
    )


@router.get("/v2/households/{household_id}/loops/{loop_id}/recipe-audio/{audio_id}")
def get_recipe_audio(
    household_id: int,
    loop_id: int,
    audio_id: str,
    session: Session = Depends(get_db_session),
) -> Response:
    """Voice a generated recipe, synthesizing on first request.

    Everything expensive happens here rather than at generation time: the
    rewrite into speakable sentences, and the Gnani call. A cook who never
    presses play costs nothing. The result is cached for the entry's
    lifetime, so replaying -- or a browser issuing range requests while
    scrubbing -- does not re-spend.

    The household and loop in the path are re-checked against the cache
    entry. An opaque id is unguessable, but this system has no
    cross-household read path anywhere else and this is not going to be the
    exception.
    """
    container = get_container()
    settings = container.settings
    entry = container.recipe_audio_cache.get(audio_id, household_id, loop_id)
    if entry is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No briefing is waiting under that id. Generated recipes are held in memory "
            "only, so it may have expired — generate the recipe again.",
        )

    with entry.lock:
        if entry.audio is not None:
            return Response(content=entry.audio, media_type=entry.media_type)

        # Resolved when the recipe was generated and carried on the entry, so
        # a settings change between POST and GET cannot leave this call with a
        # language that has no voice.
        language, voice = entry.language, entry.voice

        briefer = RecipeBriefer(container.recipe_model_provider, max_chars=settings.gnani_tts_max_chars)
        try:
            briefing = briefer.brief(
                entry.recipe,
                entry.missing_ingredients,
                language=language,
                skill_level=entry.skill_level,
            )
        except RecipeBriefingError as exc:
            _record_audio_failure(session, household_id, loop_id, f"rewrite: {exc}")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

        chunks = briefer.chunk(briefing)
        parts: list[bytes] = []
        try:
            media_type = container.voice.audio_media_type()
            for chunk in chunks:
                parts.append(
                    container.registry.invoke(
                        ToolKind.VOICE,
                        "synthesize",
                        {"cook_facing": True, "household_id": household_id},
                        text=chunk,
                        language=language,
                        voice=voice,
                    )
                )
        except ToolCallRefused as exc:
            _record_audio_failure(session, household_id, loop_id, f"refused: {exc.reason}")
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except VoiceSynthesisUnavailable as exc:
            # Not a failure: the zero-credential build working as designed.
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from exc
        except Exception as exc:
            _record_audio_failure(session, household_id, loop_id, f"synthesis: {type(exc).__name__}")
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"Speech synthesis failed: {exc}. The recipe text remains available.",
            ) from exc

        entry.spoken_text = briefing.text
        entry.media_type = media_type
        entry.audio = join_audio(parts, media_type)

    session.add(
        AuditEvent(
            household_id=household_id,
            meal_loop_id=loop_id,
            event="recipe_audio_served",
            # Deliberately no recipe text and no spoken text: routing that
            # through the audit table would persist the generated recipe by
            # the back door.
            detail=f"language={language.value} voice={voice} chunks={len(chunks)} bytes={len(entry.audio)}",
        )
    )
    session.commit()
    return Response(content=entry.audio, media_type=entry.media_type)


def _record_audio_failure(session: Session, household_id: int, loop_id: int, reason: str) -> None:
    """Reason only. Never the recipe, never the briefing text."""
    session.add(
        AuditEvent(
            household_id=household_id,
            meal_loop_id=loop_id,
            event="recipe_audio_failed",
            detail=reason[:400],
        )
    )
    session.commit()


@router.post("/households/{household_id}/loops/{loop_id}/confirm-cook")
def confirm_cook(household_id: int, loop_id: int, session: Session = Depends(get_db_session)) -> MealLoopRecord:
    repo = Repository(MealLoopRecord, session)
    loop = repo.get_for_household(loop_id, household_id)
    return repo.update(loop, {"cook_confirmed": True, "status": LoopStatus.COOKING})


@router.post("/households/{household_id}/loops/{loop_id}/outcome")
def capture_outcome(household_id: int, loop_id: int, payload: OutcomeCapture, session: Session = Depends(get_db_session)):
    """Ticket #28's happy path: both cook_confirmed and eater feedback
    close the loop, in one transaction so a crash mid-close leaves memory
    untouched rather than half-written."""
    loop_repo = Repository(MealLoopRecord, session)
    loop = loop_repo.get_for_household(loop_id, household_id)

    try:
        # session.add, NOT Repository.create: that helper commits internally
        # (app/repositories.py), which committed this history row before the
        # inventory deduction and loop update below had run. A failure after
        # that point left an orphan history row for a loop that never closed,
        # which session.rollback() could not undo -- directly contradicting
        # this function's own docstring. One commit, at the end, covers all
        # three writes.
        session.add(
            DishHistory(
                household_id=household_id,
                dish_name=payload.dish_name,
                accepted=not payload.mishap,
                rating=payload.rating,
                feedback=payload.feedback,
                leftovers_portions=payload.leftovers_portions,
            )
        )

        inventory_repo = Repository(InventoryLot, session)
        for item in payload.consumed:
            lots = [
                lot
                for lot in inventory_repo.list_for_household(household_id)
                if lot.ingredient.strip().lower() == item.name.strip().lower()
            ]
            remaining = item.quantity
            for lot in lots:
                if remaining <= 0:
                    break
                deduct = min(lot.quantity, remaining)
                lot.quantity -= deduct
                remaining -= deduct
                session.add(lot)

        loop.cook_confirmed = True
        loop.eater_feedback_captured = True
        loop.status = LoopStatus.COMPLETED
        loop.closed_at = datetime.now(UTC)
        session.add(loop)
        session.add(
            AuditEvent(
                household_id=household_id,
                meal_loop_id=loop_id,
                event="loop_closed",
                detail=f"dish={payload.dish_name} rating={payload.rating}",
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return {"status": "completed", "loop_id": loop_id}


@router.get("/households/{household_id}/reflection/weekly")
def weekly_reflection(household_id: int, session: Session = Depends(get_db_session)):
    """Ticket #28/#36's reflection surface (local form; Ticket #36 adds
    CloudWatch for SHIP IT). Names accepted, rejected, and unclosed loops."""
    since = datetime.now(UTC) - timedelta(days=7)
    loops = list(
        session.exec(
            select(MealLoopRecord).where(MealLoopRecord.household_id == household_id).where(MealLoopRecord.created_at >= since)
        )
    )
    return {
        "completed": [l.id for l in loops if l.status == LoopStatus.COMPLETED],
        "unclosed": [{"id": l.id, "reason": l.unclosed_reason} for l in loops if l.status == LoopStatus.UNCLOSED],
        "still_open": [l.id for l in loops if l.status not in LoopStatus.terminal()],
    }


# ============================================================================
# Commerce (Ticket #20)
# ============================================================================

@router.get("/households/{household_id}/commerce/search")
def commerce_search(household_id: int, query: str):
    container = get_container()
    results = {}
    if container.settings.zepto_mock_enabled:
        results["zepto"] = container.zepto.search("mock-token", query)
    results[container.commerce_second.provider_name.lower()] = container.commerce_second.search(query)
    return results


# ============================================================================
# Scheduler status (Ticket #17's degradation message, surfaced not swallowed)
# ============================================================================

@router.get("/scheduler/status")
def get_scheduler_status():
    container = get_container()
    state, detail = container.scheduler.status()
    return {"status": state, "detail": detail}

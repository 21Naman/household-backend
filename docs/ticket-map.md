# Where each build-map ticket landed

This project was built directly against a `build-map.md`, phase by phase, ticket
by ticket, with no phase skipped. This table is the map from each numbered
ticket to the code that satisfies it.

| Ticket | What it is | File(s) |
|---|---|---|
| #1 | Planning-layer audit | `README.md` + `app/api/routes.py` docstring — finding: **absent**, built fresh |
| #2 | Typed state vocabulary | `app/enums.py` |
| #3 | Managed migrations | `alembic/`, `app/database.py` |
| #4 | Household-scoped data access | `app/repositories.py` |
| #5 | Core interfaces + gated registry | `app/core/interfaces.py`, `app/core/registry.py` |
| #6 | Rail configuration surface | `app/settings.py` |
| #7 | Minimal auth gate | `app/api/deps.py`, `app/main.py::run` |
| #8 | Checkpoint: foundation holds | `tests/test_repositories.py`, `tests/test_registry.py` |
| #9 | Freshness authority rule | `app/services.py::effective_freshness` |
| #10 | Ingredient-gap calculator | `app/services.py::compute_ingredient_gap` |
| #11 | Spend-tier classifier | `app/services.py::classify_order_tier` |
| #12 | Approval gate + state machine | `app/services.py::check_execution_authorized` |
| #13 | Deterministic-logic test suite | `tests/test_services.py` (100% line coverage, import-boundary test) |
| #14 | Checkpoint: refuses correctly | same file, stale-approval + red-refusal tests |
| #15 | LocalModelProvider | `app/providers/ollama.py` |
| #16 | LocalStateStore | `app/core/state_store.py` |
| #17 | LocalScheduler | `app/core/scheduler.py` |
| #18 | Mock tiered payment authorizer | `app/providers/payments_mock.py` |
| #19 | Mock logistics / delivery-confidence | `app/providers/logistics_mock.py` |
| #20 | Second commerce provider + typed payloads | `app/providers/commerce_mock.py`, `app/providers/zepto_mcp.py`, `app/schemas.py` |
| #21 | Budget-aware consolidation | `app/services.py::consolidate_orders` |
| #22 | Checkpoint: full local loop | `tests/test_checkpoint_p2_local_loop.py` |
| #23 | Structured short-term context | `app/models.py::MealLoopRecord.guest_count/occasion` |
| #24 | Unclosed-loop detection | `app/core/unclosed_sweep.py`, `tests/test_unclosed_sweep.py` |
| #25 | Cook reply leg | `app/providers/voice.py` |
| #25a | Audio briefing (rewrite → TTS → UI) | `app/core/recipe_briefing.py`, `app/core/recipe_audio_cache.py`, `app/providers/voice_mock.py`, `app/static/index.html` |
| #26 | Approval reasons | `app/schemas.py::ApprovalDecision.reason` |
| #27 | Agent-decision tests over seed scenarios | `app/seed.py`, `tests/test_agent_decisions.py` |
| #28 | End-to-end loop closure | `tests/test_loop_e2e.py` (includes the mid-crash transaction test) |
| #28a | Loop closure from the UI | `app/static/index.html` ("I cooked this"), `app/schemas.py::ConsumedItem`, `app/api/routes.py::capture_outcome` |
| #29 | Checkpoint: BUILD IT is demoable | all of the above, green |
| #30 | AWS gate | `docs/aws-gate-evidence.md` |
| #31 | Secrets Manager | `infra/cdk/secrets_stack.py` (provisions the secrets). The runtime resolver that originally lived at `app/providers/secrets.py` was removed by a complexity audit — it had no callers and no test coverage; nothing in the app actually read a secret through it. |
| #32 | DynamoDBStateStore | `app/providers/dynamodb_state.py` |
| #33 | BedrockModelProvider | `app/providers/bedrock_model.py` |
| #34 | Lambda tool execution + IAM | `lambdas/handlers.py`, `infra/cdk/tools_stack.py` |
| #35 | EventBridge triggers | `app/providers/eventbridge_scheduler.py`, `infra/cdk/events_stack.py` |
| #36 | Observability + weekly reflection | `app/providers/observability.py`, `tests/test_observability.py` |
| #37 | Parity suite | `tests/test_parity.py` (skipped without live AWS) |
| #38 | Calendar scope verification | `app/providers/google_calendar.py`, `app/settings.py::google_calendar_scopes` |
| #39 | Delhivery Maps (live) | `app/providers/logistics_delhivery.py` |
| #40 | Pine Labs (live, **BLOCKED**) | `app/providers/payments_pinelabs.py` |
| #41 | Gnani (TTS live-capable, STT **BLOCKED** on RQ7) | `app/providers/voice_gnani.py` |
| #42 | CDK infrastructure | `infra/cdk/` (synthesizes cleanly; see `docs/aws-gate-evidence.md`) |
| #43 | Staging deploy + gate evidence | `docs/aws-gate-evidence.md` |
| #44 | Demo beats | `docs/demo-script.md` |
| #45 | Honest-limits doc | `docs/honest-limits.md` |
| #46 | Submission alignment | `docs/submission-alignment.md` |

## The three that matter most

Per the build map's own closing note:

- **#12** — the approval gate fails closed, not open. See
  `test_stale_approval_refused_when_basket_changed`.
- **#24** — the loop can never silently claim success. See
  `test_backdated_unconfirmed_loop_is_flagged_unclosed`.
- **#1** — you now actually know what the planner does, because it is this
  codebase.

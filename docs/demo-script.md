# Demo Script

Five beats, each recordable as its own clip. Lead with the cook, not the
recipe — Bible §4.5 warns the pitch could still read as "another AI meal
planner" if it opens with the recommendation layer instead.

Every number spoken or captioned below traces to a real passing test, not
memory — see the test file named next to each beat.

## Beat 1 — A fridge photo becomes confirmed inventory

`POST /api/households/{id}/inventory/capture/photo` → vision model
proposes candidates → human confirms via
`POST /api/households/{id}/inventory/capture/confirm`. Shows the trust
model before anything automated happens: nothing enters `InventoryLot`
without a confirm step.

Covered by: `app/api/routes.py::capture_inventory_from_photo` +
`confirm_inventory_capture`; exercised in
`tests/test_checkpoint_p2_local_loop.py`.

## Beat 2 — The cook brief, in the cook's language and register

`POST /api/households/{id}/loops/{loop_id}/cook-brief` for the Iyer
household renders in Tamil, at expert register for Murugan. This is the
differentiator per Bible §3.5/§3.6 — lead with it.

Covered by: `app/providers/voice.py::LocalVoiceProvider.reply`;
`tests/test_agent_decisions.py::test_every_household_cook_brief_in_their_own_language`.

## Beat 3 — Automation, then refusal

A green top-up executes with `POST .../execute-order` and **no human
tap** (Ticket #18's mock ceiling authorizer). Then attempt a red-tier
order the same way — it is **refused**, not executed, pending approval.

Beat 3's refusal is more persuasive than beat 3's automation: a system
that says no, for a stated reason, is what makes the automation
believable to a judge.

Covered by: `app/services.py::check_execution_authorized`;
`tests/test_services.py` (stale-approval + red-with-no-approval cases);
`tests/test_checkpoint_p2_local_loop.py::test_red_order_is_refused_without_approval`.

## Beat 4 — `[Track B only]` The app is stopped; EventBridge fires anyway

Stop the FastAPI process entirely. An EventBridge scheduled rule still
fires `scheduled_trigger_handler` and creates tomorrow's loop.
`LocalScheduler` (APScheduler, in-process) structurally cannot do this —
it dies with the process. This is the live proof for the AWS gate.

Covered by: `app/providers/eventbridge_scheduler.py`; requires a real AWS
deploy to actually record (see `docs/aws-gate-evidence.md` — not yet
performed in this environment).

## Beat 5 — The weekly reflection names an unclosed loop

`GET /api/households/{id}/reflection/weekly` lists completed loops,
unclosed loops with their reasons, and any blocked actions. The system
reporting its own incomplete work is the credibility beat — it's what
proves "never silently assumes success" is real, not a slogan.

Covered by: `app/core/unclosed_sweep.py`;
`tests/test_unclosed_sweep.py`; `tests/test_loop_e2e.py::test_unconfirmed_loop_becomes_unclosed_not_completed`.

---

**Rehearsal note (build map Ticket #44):** run beats 1–3 and 5 twice
consecutively with no manual intervention before presenting. Beat 3
should need no narration at all — if a viewer can't tell the refusal was
deliberate without being told, the beat isn't landing.

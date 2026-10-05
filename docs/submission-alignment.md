# Submission Alignment Pass

Checks the built system against the Bible's drafted Q3–Q7 answers and
notes where the code now supports, contradicts, or extends each one.

## Q3 — the six-step loop

Matches the built loop directly: `POST /loops` (trigger) →
`POST /loops/{id}/plan` (gap calc, tier classification, consolidation) →
approval if needed (`POST /approvals/{id}/decide`) → `POST
/loops/{id}/execute-order` → `POST /loops/{id}/cook-brief` → `POST
/loops/{id}/confirm-cook` + `POST /loops/{id}/outcome` (closes the loop).
No drift between the drafted answer and the implementation.

## Q4 — rail touches (**use the corrected framing, not the brainstorm's**)

- **Pine Labs:** describe P3P/Grantex's reserve-ceiling-once,
  debit-on-event model (Bible §3.1's correction) — this is what
  `MockPaymentProvider` (Ticket #18) implements and what
  `PineLabsPaymentProvider` (Ticket #40) is built against. **Do not**
  submit B037's original "2FA blocks variable pricing" framing as
  confirmed fact — it's contradicted by §3.1 and untested live (RQ4/RQ5).
  If the team has since run the sandbox test, use that real result
  instead of either assumption.
- **Delhivery:** describe Maps' address-validation/ETA capability, and be
  explicit that the deadline-aware *confidence score* is the team's own
  layer on top, not something the rail provides. `MockLogisticsProvider`
  and `DelhiveryLogisticsProvider` both enforce this boundary in code —
  point to it as evidence the claim is real, not aspirational.
- **Gnani:** state RQ7 plainly as open, not resolved. If a real kitchen
  audio test has been run since this codebase was written, report its
  actual result (pass or fail) rather than the Assumption Register's
  prior confidence rating.

## Q5 — the innovation claim (deadline-aware delivery-confidence)

Directly implemented: `app.providers.logistics_mock.MockLogisticsProvider
.delivery_confidence()` and its live counterpart compute confidence from
(available time before the cook's deadline) / (typical ETA), a number
Delhivery's API does not provide. `test_manual_purchase_when_delivery_confidence_is_low`
demonstrates the score changing the actual procurement path — this is a
runnable proof of the claim, not just a description of it.

## Q6 — the WhatsApp thread

Not implemented in this codebase (no WhatsApp/messaging integration was
in scope for this remediation sprint). The cook-facing channel built here
is a direct API call (`cook-brief` endpoint) with a text/TTS response —
functionally adjacent but not the same surface described in the Bible's
drafted Q6 answer. If Q6 as drafted describes WhatsApp specifically,
either build that integration before submitting or adjust the answer to
describe what was actually built.

## Q7 — whole-household monthly budgeting

`Budget` (monthly_limit, spent_amount, planned_amount,
category_allocations) and `classify_order_tier`'s use of remaining budget
directly support this. The gap: no per-transaction ledger (see
`docs/honest-limits.md`), so "whole-household" spend visibility is
currently a running total, not an itemized history. Fine for the MVP
demo; worth caveating if Q7's answer implies transaction-level detail.

## Overall

Q3–Q5 are strongly evidenced by working, tested code. Q4 specifically
needs the corrected rail framing to avoid contradicting §3.1's own
research. Q6 needs either new work or a revised answer. Q7 is
substantively supported with one honest caveat. None of this alignment
check should be read as the final word — re-run it after Tickets #40/#41's
research questions are answered and after any WhatsApp work, since both
would change what's accurate to claim.

"""Ticket #36 -- decision observability + weekly reflection (SHIP IT).

Logs carry the EXTRACTED INSTRUCTION only, never raw household WhatsApp or
voice content (Bible §4.3 privacy guardrail). `redact()` below is the
enforced boundary -- test it explicitly (tests/test_observability.py)
rather than assuming it holds.

Inside a Lambda, anything written to stdout/stderr is automatically
captured by CloudWatch Logs -- no separate PutLogEvents call is needed for
the common case, so `log_decision` below just does a structured print.
A direct boto3 CloudWatch Logs client is only needed for code running
outside Lambda (e.g. the local BUILD IT track writing to the same
function for parity in tests), which `log_decision`'s `sink` parameter
supports.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

UTC = timezone.utc

# Patterns that plausibly indicate raw conversational content rather than
# a short extracted instruction -- long free text, phone-number-shaped
# strings, or anything that looks like a WhatsApp export. This is a
# heuristic backstop, not the only control: callers should pass already-
# extracted fields (dish name, tier, reason), never a raw transcript, to
# begin with.
_LONG_TEXT_THRESHOLD = 300
_PHONE_PATTERN = re.compile(r"\+?\d[\d\-\s]{8,}\d")


class RawContentDetected(ValueError):
    pass


def redact(detail: str) -> str:
    """Raises if `detail` looks like raw conversational content rather
    than a short extracted-decision string. Callers should never need
    this to trigger in normal operation -- it exists so a mistake fails
    loudly instead of quietly leaking a transcript into CloudWatch."""
    if len(detail) > _LONG_TEXT_THRESHOLD:
        raise RawContentDetected(f"log detail exceeds {_LONG_TEXT_THRESHOLD} chars; looks like raw content, not an extracted decision")
    if _PHONE_PATTERN.search(detail):
        raise RawContentDetected("log detail contains a phone-number-shaped string; refusing to log")
    return detail


def log_decision(household_id: int, event: str, detail: str, sink: Callable[[str], None] = print) -> None:
    safe_detail = redact(detail)
    sink(json.dumps({"household_id": household_id, "event": event, "detail": safe_detail, "ts": datetime.now(UTC).isoformat()}))


@dataclass
class WeeklyReflection:
    household_id: int
    proposed: int
    accepted: int
    rejected: int
    blocked_actions: int
    unclosed_loops: list[dict[str, Any]]


def build_weekly_reflection(household_id: int, audit_events: list[Any], loops: list[Any]) -> WeeklyReflection:
    """Ticket #36: names accepted, rejected, blocked actions, and -- the
    signal Ticket #24's sweep exists to surface -- every loop flagged
    unclosed. A missing weekly reflection is indistinguishable from a
    clean one, so this function's caller (a scheduled Lambda) must run
    every week without skipping, never silently."""
    from app.enums import LoopStatus

    proposed = sum(1 for e in audit_events if e.event == "plan_computed")
    accepted = sum(1 for e in audit_events if e.event == "approval_decided" and "approved=True" in e.detail)
    rejected = sum(1 for e in audit_events if e.event == "approval_decided" and "approved=False" in e.detail)
    blocked = sum(1 for e in audit_events if e.event in ("tool_call_refused", "execution_refused"))
    unclosed = [{"id": l.id, "reason": l.unclosed_reason} for l in loops if l.status == LoopStatus.UNCLOSED]

    return WeeklyReflection(
        household_id=household_id,
        proposed=proposed,
        accepted=accepted,
        rejected=rejected,
        blocked_actions=blocked,
        unclosed_loops=unclosed,
    )

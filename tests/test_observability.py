from __future__ import annotations

import pytest

from app.enums import LoopStatus
from app.providers.observability import RawContentDetected, build_weekly_reflection, log_decision, redact


def test_redact_allows_short_extracted_decision():
    assert redact("tier=green amount=137.00") == "tier=green amount=137.00"


def test_redact_refuses_long_text_content():
    long_text = "a" * 400
    with pytest.raises(RawContentDetected):
        redact(long_text)


def test_redact_refuses_phone_number_shaped_content():
    with pytest.raises(RawContentDetected):
        redact("cook said call me at +91 98765 43210 to confirm")


def test_log_decision_uses_redact_before_sink():
    captured = []
    with pytest.raises(RawContentDetected):
        log_decision(1, "test_event", "x" * 400, sink=captured.append)
    assert captured == []  # never reached the sink


def test_log_decision_writes_json_with_household_and_event():
    captured = []
    log_decision(1, "plan_computed", "tier=green", sink=captured.append)
    import json

    payload = json.loads(captured[0])
    assert payload["household_id"] == 1
    assert payload["event"] == "plan_computed"


class _FakeEvent:
    def __init__(self, event, detail):
        self.event = event
        self.detail = detail


class _FakeLoop:
    def __init__(self, id, status, unclosed_reason=None):
        self.id = id
        self.status = status
        self.unclosed_reason = unclosed_reason


def test_weekly_reflection_names_unclosed_loops():
    events = [
        _FakeEvent("plan_computed", "tier=green"),
        _FakeEvent("approval_decided", "approved=True reason=ok"),
        _FakeEvent("approval_decided", "approved=False reason=too expensive"),
        _FakeEvent("execution_refused", "stale approval"),
    ]
    loops = [
        _FakeLoop(1, LoopStatus.COMPLETED),
        _FakeLoop(2, LoopStatus.UNCLOSED, "timed out with no cook confirmation"),
    ]
    reflection = build_weekly_reflection(household_id=1, audit_events=events, loops=loops)
    assert reflection.proposed == 1
    assert reflection.accepted == 1
    assert reflection.rejected == 1
    assert reflection.blocked_actions == 1
    assert reflection.unclosed_loops == [{"id": 2, "reason": "timed out with no cook confirmation"}]

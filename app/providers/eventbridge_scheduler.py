"""Ticket #35 -- EventBridgeScheduler.

This is the AWS gate's live demo moment (see docs/aws-gate-evidence.md):
a scheduled rule fires the daily loop with the application process
stopped, which app.core.scheduler.LocalScheduler structurally cannot do
(it dies with the process). Same EventScheduler contract as LocalScheduler
-- Ticket #37's parity suite asserts the same trigger produces the same
loop.

Loop creation triggered by an EventBridge rule must be idempotent per
household per day (a misfiring or double-firing rule must not produce two
competing loops for one dinner) -- enforced in the Lambda handler
(lambdas/scheduled_trigger_handler.py), not here; this class only manages
rule/target registration and reactive event emission.

Requires live AWS credentials to exercise; code-complete, not
live-verified in this container. See Ticket #37 / #38 (docs/aws-gate-evidence.md).
"""
from __future__ import annotations

import json


class EventBridgeUnavailable(RuntimeError):
    pass


class EventBridgeScheduler:
    def __init__(self, region: str, bus_name: str, scheduled_trigger_lambda_arn: str, sweep_lambda_arn: str):
        self.region = region
        self.bus_name = bus_name
        self.scheduled_trigger_lambda_arn = scheduled_trigger_lambda_arn
        self.sweep_lambda_arn = sweep_lambda_arn
        self._events_client = None

    def _client(self):
        if self._events_client is None:
            import boto3

            self._events_client = boto3.client("events", region_name=self.region)
        return self._events_client

    def register_daily_trigger(self, household_id: int, hour: int = 9) -> None:
        client = self._client()
        rule_name = f"household-daily-{household_id}"
        try:
            client.put_rule(
                Name=rule_name,
                ScheduleExpression=f"cron(0 {hour} * * ? *)",
                State="ENABLED",
            )
            client.put_targets(
                Rule=rule_name,
                Targets=[
                    {
                        "Id": f"scheduled-trigger-{household_id}",
                        "Arn": self.scheduled_trigger_lambda_arn,
                        "Input": json.dumps({"household_id": household_id}),
                    }
                ],
            )
        except Exception as exc:  # pragma: no cover - requires live AWS creds
            raise EventBridgeUnavailable(f"Could not register daily trigger: {exc}") from exc

    def register_sweep(self, name: str, interval_seconds: int, callback=None) -> None:
        """`callback` is unused here (EventBridge invokes a Lambda, not a
        Python callable) -- kept for signature parity with LocalScheduler
        so calling code doesn't need to branch on track. AWS does not
        support a schedule-based rule on a custom event bus, so scheduled
        rules (this and register_daily_trigger) target the default bus;
        only emit_reactive uses self.bus_name."""
        client = self._client()
        minutes = max(interval_seconds // 60, 1)
        try:
            client.put_rule(Name=name, ScheduleExpression=f"rate({minutes} minutes)", State="ENABLED")
            client.put_targets(Rule=name, Targets=[{"Id": f"{name}-target", "Arn": self.sweep_lambda_arn}])
        except Exception as exc:  # pragma: no cover - requires live AWS creds
            raise EventBridgeUnavailable(f"Could not register sweep '{name}': {exc}") from exc

    def emit_reactive(self, household_id: int, event_type: str, payload: dict) -> None:
        """Reactive events (guest arrival, cook mishap, low-stock) go to a
        custom bus. Payload carries household/loop IDs and event type
        only -- never raw household content (Bible §4.3 privacy guardrail)."""
        client = self._client()
        try:
            client.put_events(
                Entries=[
                    {
                        "Source": "household.agent",
                        "DetailType": event_type,
                        "Detail": json.dumps({"household_id": household_id, **payload}),
                        "EventBusName": self.bus_name,
                    }
                ]
            )
        except Exception as exc:  # pragma: no cover - requires live AWS creds
            raise EventBridgeUnavailable(f"Could not emit reactive event '{event_type}': {exc}") from exc

    def status(self) -> tuple[str, str | None]:
        try:
            self._client().list_rules(EventBusName=self.bus_name, Limit=1)
            return "available", None
        except Exception as exc:  # pragma: no cover - requires live AWS creds
            return "unavailable", str(exc)

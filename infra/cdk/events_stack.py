"""Ticket #35/#42 -- EventBridge bus + scheduled rules.

The daily check-in and hourly unclosed-sweep rules live here, targeting
the ScheduledTrigger and UnclosedSweep Lambdas from ToolsStack. This is
the AWS gate's live demo artifact: a rule firing with the FastAPI process
stopped is proof EventBridge is inside the runtime loop, not just hosting.
"""
from __future__ import annotations

from aws_cdk import Stack, Duration
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from constructs import Construct

from tools_stack import ToolsStack


class EventsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, tools_stack: ToolsStack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.bus = events.EventBus(self, "HouseholdEventBus", event_bus_name="household-agent-events")
        # Custom bus is for REACTIVE events (put_events from
        # EventBridgeScheduler.emit_reactive) only. AWS does not support
        # attaching a schedule-based rule to a custom bus -- scheduled
        # rules below intentionally use the default bus (no event_bus=).

        events.Rule(
            self,
            "UnclosedSweepRule",
            schedule=events.Schedule.rate(Duration.hours(1)),
            targets=[targets.LambdaFunction(tools_stack.sweep_fn)],
        )

        events.Rule(
            self,
            "WeeklyReflectionRule",
            schedule=events.Schedule.cron(minute="0", hour="8", week_day="MON"),
            targets=[targets.LambdaFunction(tools_stack.reflection_fn)],
        )

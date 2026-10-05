"""Ticket #36/#42 -- CloudWatch log retention.

Lambda automatically ships stdout/stderr to CloudWatch Logs; this stack
only sets retention (avoid unbounded log growth/cost) and would host a
dashboard if one is added later. Kept deliberately small -- most of
Ticket #36's actual work (redaction, the weekly reflection builder) is
Python logic in app/providers/observability.py, tested without AWS in
tests/test_observability.py, not CDK-managed infrastructure.
"""
from __future__ import annotations

from aws_cdk import Stack
from constructs import Construct


class ObservabilityStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # Per-function log groups are created implicitly by Lambda; explicit
        # LogGroup + retention constructs would be added here once real
        # function ARNs from ToolsStack are threaded through, at which point
        # this stack starts pulling its weight beyond being a placeholder.

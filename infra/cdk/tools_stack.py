"""Ticket #26/#34/#42 -- one Lambda per tool, own IAM role each.

The negative test (Ticket #34's acceptance criteria) is: the payments
Lambda's role cannot read the voice secret. That is enforced structurally
here -- each function only receives a grant on ITS OWN secret, never a
blanket secretsmanager:GetSecretValue on every secret in the stack.
"""
from __future__ import annotations

from aws_cdk import Stack, Duration
from aws_cdk import aws_lambda as _lambda
from constructs import Construct

from secrets_stack import SecretsStack


class ToolsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, secrets_stack: SecretsStack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        common_kwargs = dict(
            runtime=_lambda.Runtime.PYTHON_3_12,
            code=_lambda.Code.from_asset("../../lambdas"),
            timeout=Duration.seconds(30),
            memory_size=256,
        )

        self.voice_fn = _lambda.Function(self, "VoiceTool", handler="handlers.voice_handler", **common_kwargs)
        secrets_stack.secrets["household-agent/gnani"].grant_read(self.voice_fn)

        self.logistics_fn = _lambda.Function(self, "LogisticsTool", handler="handlers.logistics_handler", **common_kwargs)
        secrets_stack.secrets["household-agent/delhivery"].grant_read(self.logistics_fn)

        self.payments_fn = _lambda.Function(self, "PaymentsTool", handler="handlers.payments_handler", **common_kwargs)
        secrets_stack.secrets["household-agent/pinelabs"].grant_read(self.payments_fn)

        self.commerce_fn = _lambda.Function(self, "CommerceTool", handler="handlers.commerce_handler", **common_kwargs)
        secrets_stack.secrets["household-agent/zepto"].grant_read(self.commerce_fn)

        self.scheduled_trigger_fn = _lambda.Function(self, "ScheduledTrigger", handler="handlers.scheduled_trigger_handler", **common_kwargs)
        self.sweep_fn = _lambda.Function(self, "UnclosedSweep", handler="handlers.unclosed_sweep_handler", **common_kwargs)
        self.reflection_fn = _lambda.Function(self, "WeeklyReflection", handler="handlers.reflection_handler", **common_kwargs)

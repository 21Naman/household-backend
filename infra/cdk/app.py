#!/usr/bin/env python3
"""Ticket #42 -- CDK infrastructure entrypoint.

No secrets in the synthesized template -- everything credential-shaped is
a Secrets Manager reference (Ticket #31), never a literal value. Stacks
are kept small and separable per the build map's watch-out: forbid
console edits, since the moment someone tweaks a resource by hand, CDK
and reality diverge and the next deploy silently reverts it.

Not deployed from this container -- no AWS account/credentials are
available here (see the egress allowlist). This is code-complete,
synthesizable infrastructure, ready for `cdk deploy` in an environment
that has an AWS account. Ticket #43's docs/aws-gate-evidence.md records
that this has not yet been deployed and what deploying it would prove.
"""
from __future__ import annotations

import aws_cdk as cdk

from events_stack import EventsStack
from observability_stack import ObservabilityStack
from secrets_stack import SecretsStack
from state_stack import StateStack
from tools_stack import ToolsStack

app = cdk.App()
env = cdk.Environment(region=app.node.try_get_context("region") or "ap-south-1")

secrets = SecretsStack(app, "HouseholdAgent-Secrets", env=env)
state = StateStack(app, "HouseholdAgent-State", env=env)
observability = ObservabilityStack(app, "HouseholdAgent-Observability", env=env)
tools = ToolsStack(app, "HouseholdAgent-Tools", secrets_stack=secrets, env=env)
events = EventsStack(app, "HouseholdAgent-Events", tools_stack=tools, env=env)

app.synth()

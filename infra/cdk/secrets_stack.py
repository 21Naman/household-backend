"""Ticket #27/#31/#42 -- Secrets Manager entries.

Creates EMPTY secret shells only -- CDK never carries a literal credential
value in the synthesized template (Ticket #31's scanning test asserts
this). Real values are populated out-of-band (console, CLI, or a separate
non-CDK-tracked script) after deployment, once real rail credentials
exist. Google/Zepto keys are included for symmetry with the existing
BUILD IT settings, even though those two rails already work via .env
locally -- SHIP IT should not read credentials from environment variables
at all.
"""
from __future__ import annotations

from aws_cdk import Stack, RemovalPolicy
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class SecretsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        secret_names = [
            "household-agent/google-calendar",
            "household-agent/zepto",
            "household-agent/pinelabs",
            "household-agent/delhivery",
            "household-agent/gnani",
            "household-agent/fernet-keys",
        ]
        self.secrets = {}
        for name in secret_names:
            self.secrets[name] = secretsmanager.Secret(
                self,
                name.replace("/", "-").replace("household-agent-", ""),
                secret_name=name,
                removal_policy=RemovalPolicy.DESTROY,
                generate_secret_string=secretsmanager.SecretStringGenerator(
                    secret_string_template='{"placeholder": true}',
                    generate_string_key="_unused",
                ),
            )

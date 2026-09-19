"""Ticket #32/#42 -- DynamoDB table backing DynamoDBStateStore.

Single-table design, keyed by (pk=HOUSEHOLD#<id>, sk). On-demand billing
-- single-household demo traffic is negligible and this avoids
provisioning decisions that don't matter at this scale.
"""
from __future__ import annotations

from aws_cdk import Stack, RemovalPolicy
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct


class StateStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.table = dynamodb.Table(
            self,
            "HouseholdStateTable",
            table_name="household-agent-state",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # demo infra; not production data retention policy
        )

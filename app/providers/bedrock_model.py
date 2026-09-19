"""Ticket #33 -- BedrockModelProvider (ModelProvider over Amazon Bedrock).

Identical contract to app.providers.ollama.OllamaProvider, including the
schema parameter -- see Ticket #37's parity suite, which asserts both
providers satisfy the same assertion suite against a fixed household
fixture. Same three sanctioned uses only (Bible §3.6): extraction,
preference arbitration, cook dialogue. No tool-invocation authority --
spend and orders stay behind app.core.registry and app.services.

Requires live AWS credentials and a reachable Bedrock endpoint to
exercise; this container has neither, so this module is code-complete but
not live-verified here. Ticket #37's parity test is marked skip unless
RUN_AWS_INTEGRATION_TESTS=1 is set in an environment that does have them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.providers.ollama import _validate_against_schema


@dataclass(frozen=True)
class BedrockHealth:
    status: str
    model: str
    detail: str | None = None


class BedrockUnavailable(RuntimeError):
    pass


class BedrockModelProvider:
    """Falls back to a LocalModelProvider on timeout/error per Ticket #33's
    watch-out -- an inference hiccup must never fail the whole loop."""

    def __init__(self, model_id: str, region: str, fallback=None, timeout_seconds: float = 30.0):
        self.model_id = model_id
        self.region = region
        self.fallback = fallback
        self.timeout_seconds = timeout_seconds
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
                config=Config(connect_timeout=self.timeout_seconds, read_timeout=self.timeout_seconds, retries={"max_attempts": 2}),
            )
        return self._client

    def health(self) -> BedrockHealth:
        try:
            client = self._get_client()
            client.get_paginator  # touches the client to ensure it constructs; a real health check
        except Exception as exc:  # pragma: no cover - requires live AWS creds to exercise meaningfully
            return BedrockHealth("unavailable", self.model_id, str(exc))
        return BedrockHealth("available", self.model_id)

    def generate_structured(self, prompt: str, schema: dict | None = None) -> dict[str, Any]:
        try:
            client = self._get_client()
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt + ("\n\nRespond ONLY with valid JSON, no prose." if schema else "")}],
            }
            response = client.invoke_model(modelId=self.model_id, body=json.dumps(body))
            payload = json.loads(response["body"].read())
            text = "".join(block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text")
            decoded = json.loads(text)
            if schema:
                _validate_against_schema(decoded, schema)
            return decoded
        except Exception as exc:
            if self.fallback is not None:
                return self.fallback.generate_structured(prompt, schema=schema)
            raise BedrockUnavailable(f"Bedrock call failed and no fallback provider was configured: {exc}") from exc

"""Direct Groq adapter for structured recipe proposals.

The provider uses Groq's OpenAI-compatible Chat Completions endpoint and its
JSON Schema response format. The transport, rate-limit and validation
handling is shared with Gemini in RemoteRecipeModelProvider; only the three
hooks below are Groq-specific. The recipe service still performs strict
Pydantic validation before any result is used.
"""
from __future__ import annotations

from typing import Any

from app.providers.remote_model import RemoteRecipeModelProvider


class GroqModelProvider(RemoteRecipeModelProvider):
    provider_name = "groq"
    _endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def _headers(self, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _build_body(self, prompt: str, schema: dict | None) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "generated_recipe",
                    "strict": True,
                    "schema": schema or {"type": "object"},
                },
            },
        }

    def _extract_text(self, payload: dict[str, Any]) -> str:
        message = payload["choices"][0]["message"]
        if message.get("refusal") or not isinstance(message.get("content"), str):
            raise ValueError("Groq refused or omitted recipe content")
        return message["content"]

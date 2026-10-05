"""Direct Gemini Interactions API adapter for structured recipe proposals.

The transport, rate-limit and validation handling is shared with Groq in
RemoteRecipeModelProvider; only the three hooks below are Gemini-specific.
"""
from __future__ import annotations

from typing import Any

from app.providers.remote_model import RemoteRecipeModelProvider


class GeminiModelProvider(RemoteRecipeModelProvider):
    provider_name = "gemini"
    _endpoint = "https://generativelanguage.googleapis.com/v1beta/interactions"

    def _headers(self, api_key: str) -> dict[str, str]:
        return {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    def _build_body(self, prompt: str, schema: dict | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "input": prompt,
            "store": False,
            "generation_config": {"temperature": 0, "max_output_tokens": 2048},
        }
        if schema:
            body["response_format"] = {"type": "text", "mime_type": "application/json", "schema": schema}
        return body

    def _extract_text(self, payload: dict[str, Any]) -> str:
        """Accept the documented output_text shape and current REST aliases."""
        for key in ("output_text", "outputText"):
            value = payload.get(key)
            if isinstance(value, str):
                return value

        for collection_key in ("outputs", "output"):
            collection = payload.get(collection_key)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, dict):
                    continue
                for key in ("text", "output_text", "outputText"):
                    value = item.get(key)
                    if isinstance(value, str):
                        return value
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            return part["text"]
        raise ValueError("Gemini response lacks output text")

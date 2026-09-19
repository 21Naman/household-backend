import base64
from typing import Any

import httpx

from app.providers.ollama import validate_structured_json

INVENTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "ingredient": {"type": "string"},
                    "estimated_quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "expiry_date": {"type": ["string", "null"]},
                    "freshness": {"type": "string", "enum": ["fresh", "expiring_soon", "use_immediately"]},
                    "readability_confidence": {"type": "number"},
                    "storage_hint": {"type": "string"},
                },
                "required": ["ingredient", "estimated_quantity", "unit", "expiry_date", "freshness", "readability_confidence", "storage_hint"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


class VisionProvider:
    """Local Qwen2.5-VL adapter used only to propose inventory fields.
    Output always requires human confirmation via CapturePreview -- see
    schemas.CapturePreview.requires_confirmation."""

    def __init__(self, base_url: str, model: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def extract_inventory(self, image: bytes) -> dict[str, Any]:
        if not image:
            raise ValueError("The image file is empty or corrupt")
        prompt = (
            "Inspect this fridge or pantry photo. Return at most six clearly visible food items using the supplied JSON schema. "
            "Populate real observed values only; never use placeholder words. Estimate quantity only from what is visible. "
            "Set expiry_date to null unless a date is readable. For freshness, estimate fresh, expiring_soon, or use_immediately from visible condition; use fresh when no spoilage is visible. Set readability_confidence from 0 to 1 based on visual clarity."
        )
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": self.model, "prompt": prompt, "images": [base64.b64encode(image).decode()], "format": INVENTORY_SCHEMA, "stream": False, "options": {"temperature": 0, "num_predict": 512}},
                )
                response.raise_for_status()
                return validate_structured_json(response.json(), schema=INVENTORY_SCHEMA)
        except httpx.HTTPError as exc:
            raise RuntimeError("Local vision model is unavailable; use typed inventory entry") from exc

"""A minimal JSON-Schema checker, shared by every model provider.

Ollama, Groq and Gemini all claim to honour a supplied response schema, and
all three are capable of not doing so. This validates what came back before
it reaches Pydantic, which is the final and stricter gate.

It covers required keys, additionalProperties, types, array bounds, string
lengths and numeric bounds -- enough to catch a model ignoring the schema
without taking a dependency on a full JSON-Schema library. It lived as a
private function inside the Ollama provider even though all three providers
imported it through the underscore; it is not Ollama-specific.

Raises ValueError on any violation.
"""
from __future__ import annotations

from typing import Any


def validate_against_schema(value: Any, schema: dict) -> None:
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"Expected object, got {type(value).__name__}")
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"Missing required field '{key}' in structured response")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unexpected = set(value) - set(props)
            if unexpected:
                raise ValueError(f"Unexpected field(s): {', '.join(sorted(unexpected))}")
        for key, sub_schema in props.items():
            if key in value:
                validate_against_schema(value[key], sub_schema)
    elif schema_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"Expected array, got {type(value).__name__}")
        max_items = schema.get("maxItems")
        if max_items is not None and len(value) > max_items:
            raise ValueError(f"Array has {len(value)} items, exceeds maxItems={max_items}")
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < min_items:
            raise ValueError(f"Array has {len(value)} items, below minItems={min_items}")
        item_schema = schema.get("items")
        if item_schema:
            for item in value:
                validate_against_schema(item, item_schema)
    elif schema_type in ("string", "number", "integer", "boolean"):
        py_types = {"string": str, "number": (int, float), "integer": int, "boolean": bool}
        if not isinstance(value, py_types[schema_type]):
            raise ValueError(f"Expected {schema_type}, got {type(value).__name__}")
        if schema_type in ("number", "integer") and isinstance(value, bool):
            raise ValueError(f"Expected {schema_type}, got bool")
        if schema_type == "string":
            min_length = schema.get("minLength")
            max_length = schema.get("maxLength")
            if min_length is not None and len(value) < min_length:
                raise ValueError(f"String is shorter than minLength={min_length}")
            if max_length is not None and len(value) > max_length:
                raise ValueError(f"String is longer than maxLength={max_length}")
        if schema_type in ("number", "integer"):
            minimum = schema.get("minimum")
            exclusive_minimum = schema.get("exclusiveMinimum")
            maximum = schema.get("maximum")
            if minimum is not None and value < minimum:
                raise ValueError(f"Number is below minimum={minimum}")
            if exclusive_minimum is not None and value <= exclusive_minimum:
                raise ValueError(f"Number is not above exclusiveMinimum={exclusive_minimum}")
            if maximum is not None and value > maximum:
                raise ValueError(f"Number is above maximum={maximum}")
    elif isinstance(schema_type, list):
        # e.g. ["string", "null"]
        allowed = {"string": str, "number": (int, float), "boolean": bool, "null": type(None)}
        if not any(isinstance(value, allowed[t]) for t in schema_type if t in allowed):
            raise ValueError(f"Value does not match any of {schema_type}")

"""Prototype API-key based LLM judge provider (Anthropic).

Experimental — see docs/superpowers/specs/2026-08-25-llm-judge-prototype-design.md.
Not wired into the participant or administrator default path. This module makes a
real network call to the Anthropic API when `evaluate()` is invoked with a valid
API key; it is never invoked automatically.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_RUBRIC_ID = "project-work-v1"


class ProviderError(RuntimeError):
    """Raised when the LLM judge cannot be invoked or its output cannot be trusted."""


def probe(*, api_key_env: str = DEFAULT_API_KEY_ENV) -> dict[str, Any]:
    """Report whether an API key is configured. Makes no network call."""

    key = os.environ.get(api_key_env, "").strip()
    return {
        "provider": "anthropic",
        "api_key_env": api_key_env,
        "available": bool(key),
    }


def _require_api_key(api_key_env: str) -> str:
    key = os.environ.get(api_key_env, "").strip()
    if not key:
        raise ProviderError(
            f"environment variable {api_key_env} is required to call the Anthropic API"
        )
    return key


_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "llm-evaluation-v1.schema.json"


def _load_result_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _strip_numeric_constraints(schema: Any) -> Any:
    """Recursively remove minimum/maximum/multipleOf keys.

    Anthropic structured outputs does not support numeric range constraints;
    the caller re-validates ranges itself once the response is parsed.
    """

    if isinstance(schema, dict):
        return {
            key: _strip_numeric_constraints(value)
            for key, value in schema.items()
            if key not in ("minimum", "maximum", "multipleOf")
        }
    if isinstance(schema, list):
        return [_strip_numeric_constraints(item) for item in schema]
    return schema

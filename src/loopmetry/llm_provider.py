"""Prototype API-key based LLM judge provider (Anthropic).

Experimental — see docs/superpowers/specs/2026-08-25-llm-judge-prototype-design.md.
Not wired into the participant or administrator default path. This module makes a
real network call to the Anthropic API when `evaluate()` is invoked with a valid
API key; it is never invoked automatically.
"""

from __future__ import annotations

import json
import os
import re
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


_SCHEMA_VERSION = "0.1"
_VERDICTS = {"pass", "partial", "fail", "indeterminate"}
_SCOPES = {"session", "requirement", "project"}
_ASSESSABILITY = {"assessable", "partially_assessable", "not_assessable"}
_SEVERITIES = {"low", "medium", "high", "critical"}
_DIMENSION_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_ALLOWED_TOP_LEVEL_KEYS = {
    "schema_version", "rubric_id", "scope", "verdict", "summary",
    "dimensions", "risks", "missing_evidence", "needs_human_review",
}
_ALLOWED_DIMENSION_KEYS = {
    "key", "label", "assessability", "rating", "confidence",
    "rationale", "evidence_ids", "counterevidence_ids", "missing_evidence",
}
_ALLOWED_RISK_KEYS = {"severity", "description", "evidence_ids"}


def _require_str(value: Any, field: str, *, max_len: int, min_len: int = 1) -> str:
    if not isinstance(value, str) or not (min_len <= len(value) <= max_len):
        raise ProviderError(f"{field} must be a string of length {min_len}-{max_len}")
    return value


def _require_enum(value: Any, field: str, allowed: set[str]) -> str:
    if value not in allowed:
        raise ProviderError(f"{field} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _require_evidence_ids(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise ProviderError(f"{field} must be a list of at most 100 strings")
    for item in value:
        _require_str(item, field, max_len=200)
    if len(set(value)) != len(value):
        raise ProviderError(f"{field} must not contain duplicate IDs")
    return value


def _validate_dimension(dimension: Any) -> None:
    if not isinstance(dimension, dict):
        raise ProviderError("each dimension must be a JSON object")
    extra = set(dimension.keys()) - _ALLOWED_DIMENSION_KEYS
    if extra:
        raise ProviderError(f"dimension has unexpected keys: {sorted(extra)}")
    key = dimension.get("key")
    if not isinstance(key, str) or not _DIMENSION_KEY_RE.fullmatch(key):
        raise ProviderError(f"dimension key {key!r} does not match the required pattern")
    _require_str(dimension.get("label"), "dimension.label", max_len=120)
    _require_enum(dimension.get("assessability"), "dimension.assessability", _ASSESSABILITY)

    if "rating" not in dimension:
        raise ProviderError("dimension.rating is required (may be null, but the key must be present)")
    rating = dimension["rating"]
    if rating is not None:
        if not isinstance(rating, int) or isinstance(rating, bool) or not (0 <= rating <= 4):
            raise ProviderError("dimension.rating must be an integer 0-4 or null")

    confidence = dimension.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ProviderError("dimension.confidence must be a number 0-1")
    if not (0 <= confidence <= 1):
        raise ProviderError("dimension.confidence must be a number 0-1")

    _require_str(dimension.get("rationale"), "dimension.rationale", max_len=2000)
    _require_evidence_ids(dimension.get("evidence_ids"), "dimension.evidence_ids")
    _require_evidence_ids(dimension.get("counterevidence_ids"), "dimension.counterevidence_ids")

    missing = dimension.get("missing_evidence")
    if not isinstance(missing, list) or len(missing) > 20:
        raise ProviderError("dimension.missing_evidence must be a list of at most 20 strings")
    for item in missing:
        _require_str(item, "dimension.missing_evidence", max_len=500)


def _validate_risk(risk: Any) -> None:
    if not isinstance(risk, dict):
        raise ProviderError("each risk must be a JSON object")
    extra = set(risk.keys()) - _ALLOWED_RISK_KEYS
    if extra:
        raise ProviderError(f"risk has unexpected keys: {sorted(extra)}")
    _require_enum(risk.get("severity"), "risk.severity", _SEVERITIES)
    _require_str(risk.get("description"), "risk.description", max_len=1000)
    _require_evidence_ids(risk.get("evidence_ids"), "risk.evidence_ids")


def validate_llm_evaluation_result(raw: Any) -> dict[str, Any]:
    """Validate a parsed judge response against schemas/llm-evaluation-v1.schema.json."""

    if not isinstance(raw, dict):
        raise ProviderError("judge result must be a JSON object")

    extra = set(raw.keys()) - _ALLOWED_TOP_LEVEL_KEYS
    if extra:
        raise ProviderError(f"judge result has unexpected top-level keys: {sorted(extra)}")

    if raw.get("schema_version") != _SCHEMA_VERSION:
        raise ProviderError(f"judge result schema_version must be {_SCHEMA_VERSION!r}")
    _require_str(raw.get("rubric_id"), "rubric_id", max_len=120)
    _require_enum(raw.get("scope"), "scope", _SCOPES)
    _require_enum(raw.get("verdict"), "verdict", _VERDICTS)
    _require_str(raw.get("summary"), "summary", max_len=2000)

    dimensions = raw.get("dimensions")
    if not isinstance(dimensions, list) or not (1 <= len(dimensions) <= 12):
        raise ProviderError("dimensions must be a list of 1-12 items")
    for dimension in dimensions:
        _validate_dimension(dimension)

    risks = raw.get("risks")
    if not isinstance(risks, list) or len(risks) > 30:
        raise ProviderError("risks must be a list of at most 30 items")
    for risk in risks:
        _validate_risk(risk)

    missing_evidence = raw.get("missing_evidence")
    if not isinstance(missing_evidence, list) or len(missing_evidence) > 30:
        raise ProviderError("missing_evidence must be a list of at most 30 strings")
    for item in missing_evidence:
        _require_str(item, "missing_evidence", max_len=500)

    if not isinstance(raw.get("needs_human_review"), bool):
        raise ProviderError("needs_human_review must be a boolean")

    return dict(raw)


def check_evidence_ids(result: dict[str, Any], bundle: dict[str, Any]) -> None:
    """Raise ProviderError if any cited evidence ID is absent from the bundle."""

    events = bundle.get("events")
    if not isinstance(events, list):
        raise ProviderError("bundle has no events array to check evidence against")
    known_ids = {event.get("event_id") for event in events if isinstance(event, dict)}

    cited: set[str] = set()
    for dimension in result.get("dimensions", []):
        cited.update(dimension.get("evidence_ids", []))
        cited.update(dimension.get("counterevidence_ids", []))
    for risk in result.get("risks", []):
        cited.update(risk.get("evidence_ids", []))

    unknown = cited - known_ids
    if unknown:
        raise ProviderError(
            f"judge result cites evidence IDs not present in the bundle: {sorted(unknown)}"
        )

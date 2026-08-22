"""Canonical event schema for Loopmetry.

Loopmetry intentionally evaluates normalized project evidence rather than raw prompts.
Agent-specific adapters should emit this schema before the evaluator runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

SCHEMA_VERSION = "0.1"


class SchemaError(ValueError):
    """Raised when an event does not satisfy the canonical schema."""


class EventType(StrEnum):
    PROJECT_START = "project_start"
    PROJECT_END = "project_end"
    REQUIREMENT = "requirement"
    PLAN = "plan"
    FILE_READ = "file_read"
    FILE_CHANGE = "file_change"
    COMMAND = "command"
    VERIFICATION = "verification"
    ERROR = "error"
    HUMAN_INTERVENTION = "human_intervention"
    COMMIT = "commit"
    NOTE = "note"


class Actor(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    TOOL = "tool"
    SYSTEM = "system"


_VALID_VERIFICATION_STATUSES = {"passed", "failed", "error", "skipped"}
_VALID_COMMAND_STATUSES = {"success", "failed", "error", "unknown"}


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{field_name} must be a non-empty string")
    return value.strip()


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SchemaError("timestamp must be an ISO-8601 string")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SchemaError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise SchemaError("timestamp must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise SchemaError(f"{field_name} must be a string or list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SchemaError(f"{field_name} entries must be non-empty strings")
        result.append(item.strip())
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Event:
    """A normalized, immutable project evidence event."""

    event_id: str
    project_id: str
    session_id: str
    timestamp: datetime
    type: EventType
    actor: Actor
    source: str
    data: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Event":
        if not isinstance(raw, Mapping):
            raise SchemaError("event must be a JSON object")

        schema_version = _required_text(
            raw.get("schema_version", SCHEMA_VERSION), "schema_version"
        )
        if schema_version != SCHEMA_VERSION:
            raise SchemaError(
                f"unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION!r}"
            )

        try:
            event_type = EventType(_required_text(raw.get("type"), "type"))
        except ValueError as exc:
            allowed = ", ".join(member.value for member in EventType)
            raise SchemaError(f"unknown event type; expected one of: {allowed}") from exc

        try:
            actor = Actor(_required_text(raw.get("actor"), "actor"))
        except ValueError as exc:
            allowed = ", ".join(member.value for member in Actor)
            raise SchemaError(f"unknown actor; expected one of: {allowed}") from exc

        data = raw.get("data", {})
        if not isinstance(data, Mapping):
            raise SchemaError("data must be a JSON object")
        data_dict = dict(data)

        event = cls(
            event_id=_required_text(raw.get("event_id"), "event_id"),
            project_id=_required_text(raw.get("project_id"), "project_id"),
            session_id=_required_text(raw.get("session_id"), "session_id"),
            timestamp=_parse_timestamp(raw.get("timestamp")),
            type=event_type,
            actor=actor,
            source=_required_text(raw.get("source", "normalized"), "source"),
            data=data_dict,
            schema_version=schema_version,
        )
        event._validate_type_specific_data()
        return event

    def _validate_type_specific_data(self) -> None:
        data = self.data
        if self.type in {EventType.FILE_READ, EventType.FILE_CHANGE}:
            _required_text(data.get("path"), "data.path")
        elif self.type is EventType.REQUIREMENT:
            _required_text(data.get("requirement_id"), "data.requirement_id")
            _required_text(data.get("summary"), "data.summary")
        elif self.type is EventType.VERIFICATION:
            _required_text(data.get("kind"), "data.kind")
            status = _required_text(data.get("status"), "data.status")
            if status not in _VALID_VERIFICATION_STATUSES:
                allowed = ", ".join(sorted(_VALID_VERIFICATION_STATUSES))
                raise SchemaError(f"data.status must be one of: {allowed}")
        elif self.type is EventType.COMMAND:
            _required_text(data.get("command"), "data.command")
            status = data.get("status", "unknown")
            if status not in _VALID_COMMAND_STATUSES:
                allowed = ", ".join(sorted(_VALID_COMMAND_STATUSES))
                raise SchemaError(f"data.status must be one of: {allowed}")
        elif self.type is EventType.ERROR:
            if not data.get("message") and not data.get("code"):
                raise SchemaError("error events require data.message or data.code")
        elif self.type is EventType.HUMAN_INTERVENTION:
            _required_text(data.get("action"), "data.action")
        elif self.type is EventType.COMMIT:
            _required_text(data.get("sha"), "data.sha")

        _string_list(data.get("requirement_ids"), "data.requirement_ids")
        _string_list(data.get("paths"), "data.paths")

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        values: list[str] = []
        single = self.data.get("requirement_id")
        if isinstance(single, str) and single.strip():
            values.append(single.strip())
        values.extend(_string_list(self.data.get("requirement_ids"), "data.requirement_ids"))
        return tuple(dict.fromkeys(values))

    @property
    def paths(self) -> tuple[str, ...]:
        values: list[str] = []
        single = self.data.get("path")
        if isinstance(single, str) and single.strip():
            values.append(single.strip())
        values.extend(_string_list(self.data.get("paths"), "data.paths"))
        changed_files = self.data.get("changed_files")
        if changed_files is not None:
            values.extend(_string_list(changed_files, "data.changed_files"))
        return tuple(dict.fromkeys(values))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "type": self.type.value,
            "actor": self.actor.value,
            "source": self.source,
            "data": dict(self.data),
        }

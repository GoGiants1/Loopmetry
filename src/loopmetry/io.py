"""Input helpers for normalized Loopmetry JSONL files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .event_merge import EventConflictError, merge_events
from .schema import Event, SchemaError


class InputError(ValueError):
    """Raised when an input file cannot be parsed into events."""


def load_jsonl(path: str | Path) -> list[Event]:
    input_path = Path(path)
    if not input_path.exists():
        raise InputError(f"input file does not exist: {input_path}")
    if not input_path.is_file():
        raise InputError(f"input path is not a file: {input_path}")

    by_id: dict[str, Event] = {}
    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise InputError(
                    f"{input_path}:{line_number}: invalid JSON: {exc.msg}"
                ) from exc
            try:
                event = Event.from_mapping(raw)
            except SchemaError as exc:
                raise InputError(f"{input_path}:{line_number}: {exc}") from exc
            existing = by_id.get(event.event_id)
            if existing is None:
                by_id[event.event_id] = event
                continue
            # A repeated event_id within one file is not necessarily corrupt input:
            # hook event IDs are a deterministic hash of their payload, so a retried,
            # identical-looking hook invocation can genuinely append twice. Merge like
            # any other overlapping observation (stable invariant 10); only a real
            # content conflict is an error.
            try:
                by_id[event.event_id] = merge_events(existing, event)
            except EventConflictError as exc:
                raise InputError(
                    f"{input_path}:{line_number}: conflicting duplicate event_id "
                    f"{event.event_id!r}"
                ) from exc

    if not by_id:
        raise InputError(f"input file contains no events: {input_path}")
    return sorted(by_id.values(), key=lambda event: (event.timestamp, event.event_id))


def select_project(events: Iterable[Event], project_id: str | None = None) -> list[Event]:
    event_list = list(events)
    projects = sorted({event.project_id for event in event_list})
    if project_id is None:
        if len(projects) != 1:
            rendered = ", ".join(projects) if projects else "none"
            raise InputError(
                "project_id is required when input contains multiple projects "
                f"(found: {rendered})"
            )
        project_id = projects[0]

    selected = [event for event in event_list if event.project_id == project_id]
    if not selected:
        raise InputError(f"project not found in input: {project_id}")
    return selected

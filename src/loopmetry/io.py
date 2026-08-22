"""Input helpers for normalized Loopmetry JSONL files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .schema import Event, SchemaError


class InputError(ValueError):
    """Raised when an input file cannot be parsed into events."""


def load_jsonl(path: str | Path) -> list[Event]:
    input_path = Path(path)
    if not input_path.exists():
        raise InputError(f"input file does not exist: {input_path}")
    if not input_path.is_file():
        raise InputError(f"input path is not a file: {input_path}")

    events: list[Event] = []
    seen_ids: set[str] = set()
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
            if event.event_id in seen_ids:
                raise InputError(
                    f"{input_path}:{line_number}: duplicate event_id {event.event_id!r}"
                )
            seen_ids.add(event.event_id)
            events.append(event)

    if not events:
        raise InputError(f"input file contains no events: {input_path}")
    return sorted(events, key=lambda event: (event.timestamp, event.event_id))


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

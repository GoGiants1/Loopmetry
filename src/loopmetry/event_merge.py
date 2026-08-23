"""Shared duplicate-event merge semantics (stable invariant 10).

Overlapping observations of the same ``event_id`` must merge their provenance
without ever being mistaken for a genuine content conflict just because they
were parsed under different schema versions.
"""

from __future__ import annotations

from dataclasses import replace

from .schema import SCHEMA_VERSION, Event


class EventConflictError(ValueError):
    """Raised when two events share an ``event_id`` but disagree on content."""


def _comparable_mapping(event: Event) -> dict[str, object]:
    mapping = event.to_mapping()
    mapping.pop("provenance", None)
    mapping.pop("schema_version", None)
    return mapping


def events_conflict(existing: Event, incoming: Event) -> bool:
    """Return True when two same-``event_id`` events disagree beyond provenance."""

    return _comparable_mapping(existing) != _comparable_mapping(incoming)


def merge_events(existing: Event, incoming: Event) -> Event:
    """Merge ``incoming`` into ``existing``, deduping provenance by equality.

    Raises ``EventConflictError`` if the events disagree on anything other
    than provenance or schema version. The merged event's ``schema_version``
    is normalized to the current ``SCHEMA_VERSION``.
    """

    if events_conflict(existing, incoming):
        raise EventConflictError(f"conflicting duplicate event_id {existing.event_id!r}")

    merged = list(existing.provenance)
    seen = [record.to_mapping() for record in merged]
    for record in incoming.provenance:
        mapping = record.to_mapping()
        if mapping not in seen:
            merged.append(record)
            seen.append(mapping)

    if len(merged) == len(existing.provenance) and existing.schema_version == SCHEMA_VERSION:
        return existing
    return replace(existing, provenance=tuple(merged), schema_version=SCHEMA_VERSION)

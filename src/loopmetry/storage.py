"""SQLite evidence store for normalized Loopmetry events."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .event_merge import EventConflictError, merge_events
from .schema import Event


class StorageError(ValueError):
    """Raised when persisted evidence cannot be reconciled with new evidence."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    inserted: int
    merged: int
    skipped: int


class EventStore:
    """Small local-only event store.

    The store persists normalized evidence, not raw agent transcripts. Callers are
    responsible for applying any organization-specific retention policy.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                project_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                source TEXT NOT NULL,
                data_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_project_time "
            "ON events(project_id, timestamp, event_id)"
        )
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(events)")}
        if "provenance_json" not in columns:
            self._connection.execute(
                "ALTER TABLE events ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '[]'"
            )
        self._connection.commit()

    def _row_to_event(self, row: sqlite3.Row) -> Event:
        return Event.from_mapping(
            {
                "event_id": row["event_id"],
                "schema_version": row["schema_version"],
                "project_id": row["project_id"],
                "session_id": row["session_id"],
                "timestamp": row["timestamp"],
                "type": row["event_type"],
                "actor": row["actor"],
                "source": row["source"],
                "data": json.loads(row["data_json"]),
                "provenance": json.loads(row["provenance_json"]),
            }
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def add_events(self, events: Iterable[Event]) -> IngestResult:
        inserted = 0
        merged = 0
        skipped = 0
        with self._connection:
            for event in events:
                row = self._connection.execute(
                    "SELECT * FROM events WHERE event_id = ?", (event.event_id,)
                ).fetchone()
                if row is None:
                    self._connection.execute(
                        """
                        INSERT INTO events (
                            event_id, schema_version, project_id, session_id,
                            timestamp, event_type, actor, source, data_json,
                            provenance_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            event.schema_version,
                            event.project_id,
                            event.session_id,
                            event.timestamp.isoformat(),
                            event.type.value,
                            event.actor.value,
                            event.source,
                            json.dumps(event.data, ensure_ascii=False, sort_keys=True),
                            json.dumps(
                                [record.to_mapping() for record in event.provenance],
                                ensure_ascii=False,
                            ),
                        ),
                    )
                    inserted += 1
                    continue

                existing = self._row_to_event(row)
                try:
                    merged_event = merge_events(existing, event)
                except EventConflictError as exc:
                    raise StorageError(str(exc)) from exc
                if merged_event is existing:
                    skipped += 1
                    continue
                self._connection.execute(
                    "UPDATE events SET schema_version = ?, provenance_json = ? "
                    "WHERE event_id = ?",
                    (
                        merged_event.schema_version,
                        json.dumps(
                            [record.to_mapping() for record in merged_event.provenance],
                            ensure_ascii=False,
                        ),
                        event.event_id,
                    ),
                )
                merged += 1
        return IngestResult(inserted=inserted, merged=merged, skipped=skipped)

    def list_events(self, project_id: str) -> list[Event]:
        rows = self._connection.execute(
            """
            SELECT event_id, schema_version, project_id, session_id,
                   timestamp, event_type, actor, source, data_json, provenance_json
            FROM events
            WHERE project_id = ?
            ORDER BY timestamp ASC, event_id ASC
            """,
            (project_id,),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_projects(self) -> list[tuple[str, int]]:
        rows = self._connection.execute(
            """
            SELECT project_id, COUNT(*) AS event_count
            FROM events
            GROUP BY project_id
            ORDER BY project_id ASC
            """
        ).fetchall()
        return [(row["project_id"], int(row["event_count"])) for row in rows]

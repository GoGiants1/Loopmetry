"""SQLite evidence store for normalized Loopmetry events."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .schema import Event


@dataclass(frozen=True, slots=True)
class IngestResult:
    inserted: int
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
                data_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_project_time "
            "ON events(project_id, timestamp, event_id)"
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def add_events(self, events: Iterable[Event]) -> IngestResult:
        inserted = 0
        skipped = 0
        with self._connection:
            for event in events:
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        event_id, schema_version, project_id, session_id,
                        timestamp, event_type, actor, source, data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1
        return IngestResult(inserted=inserted, skipped=skipped)

    def list_events(self, project_id: str) -> list[Event]:
        rows = self._connection.execute(
            """
            SELECT event_id, schema_version, project_id, session_id,
                   timestamp, event_type, actor, source, data_json
            FROM events
            WHERE project_id = ?
            ORDER BY timestamp ASC, event_id ASC
            """,
            (project_id,),
        ).fetchall()
        return [
            Event.from_mapping(
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
                }
            )
            for row in rows
        ]

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

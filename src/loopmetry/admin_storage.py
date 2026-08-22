"""SQLite persistence for participant enrollment and collected submissions."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .submission import normalize_identifier, validate_submission

REVIEW_STATUSES = ("received", "reviewing", "needs_revision", "accepted")


class AdminStorageError(ValueError):
    """Raised when administrator data cannot be stored or updated."""


@dataclass(frozen=True, slots=True)
class Enrollment:
    assignment_id: str
    submitter_id: str
    display_name: str
    token: str
    token_hint: str
    rotated: bool

    def to_mapping(self) -> dict[str, object]:
        return {
            "assignment_id": self.assignment_id,
            "submitter_id": self.submitter_id,
            "display_name": self.display_name,
            "submission_token": self.token,
            "token_hint": self.token_hint,
            "rotated": self.rotated,
        }


@dataclass(frozen=True, slots=True)
class Participant:
    assignment_id: str
    submitter_id: str
    display_name: str
    token_hint: str
    active: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class StoredSubmission:
    submission_id: str
    assignment_id: str
    submitter_id: str
    display_name: str
    project_id: str
    attempt: int
    status: str
    reviewer_note: str
    client_created_at: str
    received_at: str
    event_count: int
    session_count: int
    measurement_gap_count: int
    metrics: tuple[dict[str, object], ...]
    envelope: dict[str, Any]

    def summary_mapping(self) -> dict[str, object]:
        return {
            "submission_id": self.submission_id,
            "assignment_id": self.assignment_id,
            "submitter_id": self.submitter_id,
            "display_name": self.display_name,
            "project_id": self.project_id,
            "attempt": self.attempt,
            "status": self.status,
            "reviewer_note": self.reviewer_note,
            "client_created_at": self.client_created_at,
            "received_at": self.received_at,
            "event_count": self.event_count,
            "session_count": self.session_count,
            "measurement_gap_count": self.measurement_gap_count,
            "metrics": list(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class SubmissionInsertResult:
    stored: StoredSubmission
    duplicate: bool


@dataclass(frozen=True, slots=True)
class ParticipantOverview:
    participant: Participant
    latest: StoredSubmission | None

    @property
    def state(self) -> str:
        return self.latest.status if self.latest else "not_submitted"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return "lm_" + secrets.token_urlsafe(24)


def _token_hint(token: str) -> str:
    return token[:8] + "…" + token[-4:]


def _metric_summary(envelope: Mapping[str, Any]) -> tuple[list[dict[str, object]], int]:
    report = envelope["report"]
    metrics: list[dict[str, object]] = []
    gap_count = len(report.get("measurement_gaps", []))
    for metric in report.get("metrics", []):
        metrics.append(
            {
                "key": metric["key"],
                "title": metric.get("title", metric["key"]),
                "score": float(metric["score"]),
                "confidence": float(metric["confidence"]),
            }
        )
        gaps = metric.get("gaps", [])
        if isinstance(gaps, list):
            gap_count += len(gaps)
    return metrics, gap_count


class AdminStore:
    """Thread-safe-by-connection SQLite repository for the administrator service."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS participants (
                    assignment_id TEXT NOT NULL,
                    submitter_id TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    token_hash TEXT NOT NULL UNIQUE,
                    token_hint TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (assignment_id, submitter_id)
                );

                CREATE TABLE IF NOT EXISTS submissions (
                    submission_id TEXT PRIMARY KEY,
                    assignment_id TEXT NOT NULL,
                    submitter_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reviewer_note TEXT NOT NULL DEFAULT '',
                    client_created_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    event_count INTEGER NOT NULL,
                    session_count INTEGER NOT NULL,
                    measurement_gap_count INTEGER NOT NULL,
                    metrics_json TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    FOREIGN KEY (assignment_id, submitter_id)
                        REFERENCES participants (assignment_id, submitter_id),
                    UNIQUE (assignment_id, submitter_id, attempt)
                );

                CREATE INDEX IF NOT EXISTS submissions_participant_idx
                    ON submissions (assignment_id, submitter_id, attempt DESC);
                CREATE INDEX IF NOT EXISTS submissions_status_idx
                    ON submissions (assignment_id, status, received_at DESC);

                CREATE TABLE IF NOT EXISTS status_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    submission_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    changed_at TEXT NOT NULL,
                    FOREIGN KEY (submission_id) REFERENCES submissions (submission_id)
                );
                """
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def enroll(
        self,
        *,
        assignment_id: str,
        submitter_id: str,
        display_name: str = "",
        rotate: bool = False,
    ) -> Enrollment:
        assignment_id = normalize_identifier(assignment_id, "assignment_id")
        submitter_id = normalize_identifier(submitter_id, "submitter_id")
        display_name = display_name.strip()
        if len(display_name) > 200:
            raise AdminStorageError("display_name must be at most 200 characters")
        token = _new_token()
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM participants WHERE assignment_id = ? AND submitter_id = ?",
                (assignment_id, submitter_id),
            ).fetchone()
            if existing and not rotate:
                raise AdminStorageError(
                    f"participant already enrolled: {assignment_id}/{submitter_id}; use --rotate"
                )
            if existing:
                connection.execute(
                    """
                    UPDATE participants
                    SET display_name = ?, token_hash = ?, token_hint = ?, active = 1
                    WHERE assignment_id = ? AND submitter_id = ?
                    """,
                    (
                        display_name,
                        _token_hash(token),
                        _token_hint(token),
                        assignment_id,
                        submitter_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO participants (
                        assignment_id, submitter_id, display_name,
                        token_hash, token_hint, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        assignment_id,
                        submitter_id,
                        display_name,
                        _token_hash(token),
                        _token_hint(token),
                        now,
                    ),
                )
            connection.commit()
        return Enrollment(
            assignment_id=assignment_id,
            submitter_id=submitter_id,
            display_name=display_name,
            token=token,
            token_hint=_token_hint(token),
            rotated=bool(existing),
        )

    def enroll_many(
        self,
        *,
        assignment_id: str,
        participants: Sequence[tuple[str, str]],
    ) -> list[Enrollment]:
        assignment_id = normalize_identifier(assignment_id, "assignment_id")
        if not participants:
            raise AdminStorageError("roster contains no participants")
        normalized_participants: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_submitter_id, raw_display_name in participants:
            submitter_id = normalize_identifier(raw_submitter_id, "submitter_id")
            display_name = raw_display_name.strip()
            if len(display_name) > 200:
                raise AdminStorageError("display_name must be at most 200 characters")
            if submitter_id in seen:
                raise AdminStorageError(f"duplicate submitter_id in roster: {submitter_id}")
            seen.add(submitter_id)
            normalized_participants.append((submitter_id, display_name))

        created: list[Enrollment] = []
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_rows = connection.execute(
                "SELECT submitter_id FROM participants WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchall()
            existing = {str(row["submitter_id"]) for row in existing_rows}
            duplicates = sorted(existing & seen)
            if duplicates:
                raise AdminStorageError(
                    "participants already enrolled: " + ", ".join(duplicates)
                )
            for submitter_id, display_name in normalized_participants:
                token = _new_token()
                hint = _token_hint(token)
                connection.execute(
                    """
                    INSERT INTO participants (
                        assignment_id, submitter_id, display_name,
                        token_hash, token_hint, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        assignment_id,
                        submitter_id,
                        display_name,
                        _token_hash(token),
                        hint,
                        now,
                    ),
                )
                created.append(
                    Enrollment(
                        assignment_id=assignment_id,
                        submitter_id=submitter_id,
                        display_name=display_name,
                        token=token,
                        token_hint=hint,
                        rotated=False,
                    )
                )
            connection.commit()
        return created

    def authenticate(self, token: str) -> Participant | None:
        normalized = token.strip()
        if not normalized:
            return None
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT assignment_id, submitter_id, display_name, token_hint, active, created_at
                FROM participants WHERE token_hash = ?
                """,
                (_token_hash(normalized),),
            ).fetchone()
        if row is None or not bool(row["active"]):
            return None
        return Participant(
            assignment_id=str(row["assignment_id"]),
            submitter_id=str(row["submitter_id"]),
            display_name=str(row["display_name"]),
            token_hint=str(row["token_hint"]),
            active=bool(row["active"]),
            created_at=str(row["created_at"]),
        )

    def add_submission(
        self,
        envelope: Mapping[str, Any],
        *,
        participant: Participant,
    ) -> SubmissionInsertResult:
        validated = validate_submission(
            envelope,
            expected_assignment_id=participant.assignment_id,
            expected_submitter_id=participant.submitter_id,
        )
        metrics, gap_count = _metric_summary(validated)
        snapshot = validated["report"]["snapshot"]
        received_at = _utc_now()

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate_row = connection.execute(
                "SELECT * FROM submissions WHERE submission_id = ?",
                (validated["submission_id"],),
            ).fetchone()
            if duplicate_row is not None:
                stored = self._row_to_submission(connection, duplicate_row)
                connection.commit()
                return SubmissionInsertResult(stored=stored, duplicate=True)

            next_attempt = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(attempt), 0) + 1 AS next_attempt
                    FROM submissions WHERE assignment_id = ? AND submitter_id = ?
                    """,
                    (participant.assignment_id, participant.submitter_id),
                ).fetchone()["next_attempt"]
            )
            connection.execute(
                """
                INSERT INTO submissions (
                    submission_id, assignment_id, submitter_id, project_id,
                    attempt, status, reviewer_note, client_created_at, received_at,
                    event_count, session_count, measurement_gap_count,
                    metrics_json, envelope_json
                ) VALUES (?, ?, ?, ?, ?, 'received', '', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validated["submission_id"],
                    participant.assignment_id,
                    participant.submitter_id,
                    validated["project_id"],
                    next_attempt,
                    validated["created_at"],
                    received_at,
                    int(snapshot["event_count"]),
                    int(snapshot["session_count"]),
                    gap_count,
                    json.dumps(metrics, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(validated, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            connection.execute(
                """
                INSERT INTO status_history (submission_id, status, note, changed_at)
                VALUES (?, 'received', '', ?)
                """,
                (validated["submission_id"], received_at),
            )
            row = connection.execute(
                "SELECT * FROM submissions WHERE submission_id = ?",
                (validated["submission_id"],),
            ).fetchone()
            stored = self._row_to_submission(connection, row)
            connection.commit()
        return SubmissionInsertResult(stored=stored, duplicate=False)

    def _row_to_submission(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> StoredSubmission:
        participant = connection.execute(
            """
            SELECT display_name FROM participants
            WHERE assignment_id = ? AND submitter_id = ?
            """,
            (row["assignment_id"], row["submitter_id"]),
        ).fetchone()
        metrics_raw = json.loads(str(row["metrics_json"]))
        envelope_raw = json.loads(str(row["envelope_json"]))
        return StoredSubmission(
            submission_id=str(row["submission_id"]),
            assignment_id=str(row["assignment_id"]),
            submitter_id=str(row["submitter_id"]),
            display_name=str(participant["display_name"] if participant else ""),
            project_id=str(row["project_id"]),
            attempt=int(row["attempt"]),
            status=str(row["status"]),
            reviewer_note=str(row["reviewer_note"]),
            client_created_at=str(row["client_created_at"]),
            received_at=str(row["received_at"]),
            event_count=int(row["event_count"]),
            session_count=int(row["session_count"]),
            measurement_gap_count=int(row["measurement_gap_count"]),
            metrics=tuple(dict(item) for item in metrics_raw),
            envelope=dict(envelope_raw),
        )

    def get_submission(self, submission_id: str) -> StoredSubmission | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            return self._row_to_submission(connection, row) if row is not None else None

    def update_status(self, submission_id: str, status: str, note: str = "") -> StoredSubmission:
        if status not in REVIEW_STATUSES:
            raise AdminStorageError(
                "status must be one of: " + ", ".join(REVIEW_STATUSES)
            )
        normalized_note = note.strip()
        if len(normalized_note) > 4_000:
            raise AdminStorageError("reviewer note must be at most 4000 characters")
        changed_at = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE submissions SET status = ?, reviewer_note = ?
                WHERE submission_id = ?
                """,
                (status, normalized_note, submission_id),
            )
            if updated.rowcount != 1:
                raise AdminStorageError(f"submission not found: {submission_id}")
            connection.execute(
                """
                INSERT INTO status_history (submission_id, status, note, changed_at)
                VALUES (?, ?, ?, ?)
                """,
                (submission_id, status, normalized_note, changed_at),
            )
            row = connection.execute(
                "SELECT * FROM submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            stored = self._row_to_submission(connection, row)
            connection.commit()
        return stored

    def list_overview(
        self,
        *,
        assignment_id: str | None = None,
        status: str | None = None,
        query: str | None = None,
    ) -> list[ParticipantOverview]:
        parameters: list[object] = []
        conditions = ["p.active = 1"]
        if assignment_id:
            conditions.append("p.assignment_id = ?")
            parameters.append(assignment_id)
        normalized_query = query.strip() if query else ""
        if normalized_query:
            conditions.append(
                "(p.submitter_id LIKE ? ESCAPE '\\' OR p.display_name LIKE ? ESCAPE '\\')"
            )
            escaped = (
                normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            parameters.extend((f"%{escaped}%", f"%{escaped}%"))

        sql = f"""
            WITH latest_attempt AS (
                SELECT assignment_id, submitter_id, MAX(attempt) AS attempt
                FROM submissions
                GROUP BY assignment_id, submitter_id
            )
            SELECT
                p.assignment_id AS p_assignment_id,
                p.submitter_id AS p_submitter_id,
                p.display_name AS p_display_name,
                p.token_hint AS p_token_hint,
                p.active AS p_active,
                p.created_at AS p_created_at,
                s.*
            FROM participants p
            LEFT JOIN latest_attempt la
              ON la.assignment_id = p.assignment_id
             AND la.submitter_id = p.submitter_id
            LEFT JOIN submissions s
              ON s.assignment_id = la.assignment_id
             AND s.submitter_id = la.submitter_id
             AND s.attempt = la.attempt
            WHERE {' AND '.join(conditions)}
            ORDER BY p.assignment_id, p.submitter_id
        """
        results: list[ParticipantOverview] = []
        with self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
            for row in rows:
                latest: StoredSubmission | None = None
                if row["submission_id"] is not None:
                    latest = self._row_to_submission(connection, row)
                overview = ParticipantOverview(
                    participant=Participant(
                        assignment_id=str(row["p_assignment_id"]),
                        submitter_id=str(row["p_submitter_id"]),
                        display_name=str(row["p_display_name"]),
                        token_hint=str(row["p_token_hint"]),
                        active=bool(row["p_active"]),
                        created_at=str(row["p_created_at"]),
                    ),
                    latest=latest,
                )
                if status and overview.state != status:
                    continue
                results.append(overview)
        return results

    def list_submission_history(
        self,
        *,
        assignment_id: str,
        submitter_id: str,
    ) -> list[StoredSubmission]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM submissions
                WHERE assignment_id = ? AND submitter_id = ?
                ORDER BY attempt DESC
                """,
                (assignment_id, submitter_id),
            ).fetchall()
            return [self._row_to_submission(connection, row) for row in rows]

    def list_assignments(self) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT assignment_id FROM participants ORDER BY assignment_id"
            ).fetchall()
        return [str(row["assignment_id"]) for row in rows]

    def status_history(self, submission_id: str) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT status, note, changed_at FROM status_history
                WHERE submission_id = ? ORDER BY id DESC
                """,
                (submission_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def dashboard_counts(self, *, assignment_id: str | None = None) -> dict[str, int]:
        overview = self.list_overview(assignment_id=assignment_id)
        counts = {"total": len(overview), "not_submitted": 0}
        counts.update({status: 0 for status in REVIEW_STATUSES})
        for item in overview:
            counts[item.state] = counts.get(item.state, 0) + 1
        return counts

    def export_rows(self, *, assignment_id: str | None = None) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for overview in self.list_overview(assignment_id=assignment_id):
            latest = overview.latest
            metric_values = {
                str(metric["key"]): metric for metric in (latest.metrics if latest else ())
            }
            row: dict[str, object] = {
                "assignment_id": overview.participant.assignment_id,
                "submitter_id": overview.participant.submitter_id,
                "display_name": overview.participant.display_name,
                "state": overview.state,
                "submission_id": latest.submission_id if latest else "",
                "attempt": latest.attempt if latest else "",
                "project_id": latest.project_id if latest else "",
                "received_at": latest.received_at if latest else "",
                "event_count": latest.event_count if latest else "",
                "session_count": latest.session_count if latest else "",
                "measurement_gap_count": latest.measurement_gap_count if latest else "",
            }
            for key, metric in sorted(metric_values.items()):
                row[f"metric_{key}_score"] = metric["score"]
                row[f"metric_{key}_confidence"] = metric["confidence"]
            rows.append(row)
        return rows

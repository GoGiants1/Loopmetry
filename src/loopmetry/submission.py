"""Participant submission envelopes and HTTP transport.

The submission contract intentionally excludes raw canonical events.  Administrators
receive a deterministic report, compact provenance, and privacy declarations rather
than the participant's full agent transcript or source tree.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from . import __version__
from .evaluation_models import ProjectReport
from .schema import Event

SUBMISSION_SCHEMA_VERSION = "1.0"
DEFAULT_SUBMISSION_TOKEN_ENV = "LOOPMETRY_SUBMISSION_TOKEN"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class SubmissionError(ValueError):
    """Raised when a submission envelope or transport is invalid."""


@dataclass(frozen=True, slots=True)
class SubmissionReceipt:
    submission_id: str
    assignment_id: str
    submitter_id: str
    attempt: int
    status: str
    duplicate: bool
    received_at: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SubmissionReceipt":
        try:
            return cls(
                submission_id=str(raw["submission_id"]),
                assignment_id=str(raw["assignment_id"]),
                submitter_id=str(raw["submitter_id"]),
                attempt=int(raw["attempt"]),
                status=str(raw["status"]),
                duplicate=bool(raw.get("duplicate", False)),
                received_at=str(raw["received_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SubmissionError("server returned an invalid submission receipt") from exc

    def to_mapping(self) -> dict[str, object]:
        return {
            "submission_id": self.submission_id,
            "assignment_id": self.assignment_id,
            "submitter_id": self.submitter_id,
            "attempt": self.attempt,
            "status": self.status,
            "duplicate": self.duplicate,
            "received_at": self.received_at,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_identifier(value: str, field_name: str = "identifier") -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or not _ID_RE.fullmatch(normalized):
        raise SubmissionError(
            f"{field_name} must match {_ID_RE.pattern!r} and be at most 128 characters"
        )
    return normalized


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SubmissionError(f"submission is not canonical JSON: {exc}") from exc
    return rendered.encode("utf-8")


def _submission_digest(payload_without_id: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(payload_without_id)).hexdigest()


def _parse_aware_timestamp(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SubmissionError(f"{field_name} must be an ISO-8601 timestamp")
    normalized = value.strip()
    candidate = normalized[:-1] + "+00:00" if normalized.endswith("Z") else normalized
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise SubmissionError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SubmissionError(f"{field_name} must include a timezone")
    return _iso(parsed)


def _source_summary(events: Sequence[Event], source_file_count: int) -> dict[str, object]:
    return {
        "event_count": len(events),
        "session_count": len({event.session_id for event in events}),
        "sources": sorted({event.source for event in events}),
        "source_file_count": int(source_file_count),
        "started_at": _iso(min(event.timestamp for event in events)),
        "ended_at": _iso(max(event.timestamp for event in events)),
    }


def build_submission(
    report: ProjectReport,
    events: Sequence[Event],
    *,
    assignment_id: str,
    submitter_id: str,
    source_file_count: int,
    created_at: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build a content-addressed v1 participant submission envelope."""

    if not events:
        raise SubmissionError("at least one event is required to build a submission")
    assignment = normalize_identifier(assignment_id, "assignment_id")
    submitter = normalize_identifier(submitter_id, "submitter_id")
    if source_file_count < 1:
        raise SubmissionError("source_file_count must be at least 1")
    event_projects = {event.project_id for event in events}
    if event_projects != {report.project_id}:
        raise SubmissionError("report project_id does not match the submitted events")

    envelope_without_id: dict[str, Any] = {
        "schema_version": SUBMISSION_SCHEMA_VERSION,
        "assignment_id": assignment,
        "submitter_id": submitter,
        "project_id": report.project_id,
        "run_id": run_id or f"run-{uuid4().hex}",
        "created_at": _iso(created_at or _utc_now()),
        "client": {
            "name": "loopmetry",
            "version": __version__,
        },
        "input_summary": _source_summary(events, source_file_count),
        "report": report.to_mapping(),
        "privacy": {
            "raw_transcripts_included": False,
            "raw_source_code_included": False,
            "canonical_events_included": False,
            "absolute_source_paths_included": False,
        },
    }
    submission_id = _submission_digest(envelope_without_id)
    return {
        "schema_version": SUBMISSION_SCHEMA_VERSION,
        "submission_id": submission_id,
        **{key: value for key, value in envelope_without_id.items() if key != "schema_version"},
    }


def validate_submission(
    raw: Mapping[str, Any],
    *,
    expected_assignment_id: str | None = None,
    expected_submitter_id: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize one v1 submission envelope.

    The returned mapping is safe to persist as the authoritative envelope.  Unknown
    top-level keys are preserved for forward-compatible metadata, but the content hash
    covers them as well.
    """

    if not isinstance(raw, Mapping):
        raise SubmissionError("submission must be a JSON object")
    value = dict(raw)
    if value.get("schema_version") != SUBMISSION_SCHEMA_VERSION:
        raise SubmissionError(
            f"unsupported submission schema_version; expected {SUBMISSION_SCHEMA_VERSION!r}"
        )

    submission_id = value.get("submission_id")
    if not isinstance(submission_id, str) or not _HASH_RE.fullmatch(submission_id):
        raise SubmissionError("submission_id must be a sha256 content identifier")

    assignment = normalize_identifier(str(value.get("assignment_id", "")), "assignment_id")
    submitter = normalize_identifier(str(value.get("submitter_id", "")), "submitter_id")
    project = normalize_identifier(str(value.get("project_id", "")), "project_id")
    normalize_identifier(str(value.get("run_id", "")), "run_id")
    value["created_at"] = _parse_aware_timestamp(value.get("created_at"), "created_at")

    if expected_assignment_id is not None and assignment != expected_assignment_id:
        raise SubmissionError("submission assignment_id does not match the enrollment token")
    if expected_submitter_id is not None and submitter != expected_submitter_id:
        raise SubmissionError("submission submitter_id does not match the enrollment token")

    client = value.get("client")
    if not isinstance(client, Mapping) or client.get("name") != "loopmetry":
        raise SubmissionError("client must identify Loopmetry")
    if not isinstance(client.get("version"), str) or not str(client.get("version")).strip():
        raise SubmissionError("client.version must be present")

    summary = value.get("input_summary")
    if not isinstance(summary, Mapping):
        raise SubmissionError("input_summary must be a JSON object")
    for field in ("event_count", "session_count", "source_file_count"):
        item = summary.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise SubmissionError(f"input_summary.{field} must be a non-negative integer")
    if summary.get("event_count", 0) < 1 or summary.get("source_file_count", 0) < 1:
        raise SubmissionError("input_summary must describe at least one event and source file")
    sources = summary.get("sources")
    if not isinstance(sources, list) or any(
        not isinstance(item, str) or not item.strip() for item in sources
    ):
        raise SubmissionError("input_summary.sources must be a list of non-empty strings")
    _parse_aware_timestamp(summary.get("started_at"), "input_summary.started_at")
    _parse_aware_timestamp(summary.get("ended_at"), "input_summary.ended_at")

    report = value.get("report")
    if not isinstance(report, Mapping):
        raise SubmissionError("report must be a JSON object")
    if report.get("project_id") != project:
        raise SubmissionError("report.project_id must match project_id")
    if "overall_score" in report:
        raise SubmissionError("report must not contain a universal overall_score")
    snapshot = report.get("snapshot")
    metrics = report.get("metrics")
    if not isinstance(snapshot, Mapping) or not isinstance(metrics, list) or not metrics:
        raise SubmissionError("report must include snapshot and non-empty metrics")
    if snapshot.get("event_count") != summary.get("event_count"):
        raise SubmissionError("report snapshot event_count must match input_summary")
    for metric in metrics:
        if not isinstance(metric, Mapping):
            raise SubmissionError("report.metrics entries must be JSON objects")
        if not isinstance(metric.get("key"), str) or not metric.get("key"):
            raise SubmissionError("each report metric requires a key")
        score = metric.get("score")
        confidence = metric.get("confidence")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100:
            raise SubmissionError("metric scores must be in the range 0..100")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= confidence <= 1
        ):
            raise SubmissionError("metric confidence must be in the range 0..1")

    privacy = value.get("privacy")
    expected_privacy = {
        "raw_transcripts_included": False,
        "raw_source_code_included": False,
        "canonical_events_included": False,
        "absolute_source_paths_included": False,
    }
    if not isinstance(privacy, Mapping) or any(
        privacy.get(key) is not expected for key, expected in expected_privacy.items()
    ):
        raise SubmissionError("submission privacy declarations do not satisfy the v1 policy")

    payload_without_id = dict(value)
    payload_without_id.pop("submission_id", None)
    expected_digest = _submission_digest(payload_without_id)
    if not hmac.compare_digest(submission_id, expected_digest):
        raise SubmissionError("submission_id does not match the envelope content")

    value["assignment_id"] = assignment
    value["submitter_id"] = submitter
    value["project_id"] = project
    return value


def render_submission(envelope: Mapping[str, Any]) -> str:
    validated = validate_submission(envelope)
    return json.dumps(validated, ensure_ascii=False, indent=2) + "\n"


def write_private_text(path: str | Path, content: str) -> Path:
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    try:
        output.chmod(0o600)
    except OSError:
        pass
    return output


def load_submission(path: str | Path) -> dict[str, Any]:
    input_path = Path(path).expanduser()
    try:
        raw = json.loads(input_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SubmissionError(f"cannot read submission file {input_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SubmissionError(f"submission file is invalid JSON: {exc}") from exc
    return validate_submission(raw)


def _validate_server_url(server_url: str) -> str:
    normalized = server_url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SubmissionError("server URL must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SubmissionError("server URL must not contain credentials, query, or fragment")
    if parsed.scheme == "http" and parsed.hostname.lower() not in _LOOPBACK_HOSTS:
        raise SubmissionError(
            "refusing to send an enrollment token over plaintext HTTP to a non-loopback host"
        )
    return normalized


def submit_envelope(
    server_url: str,
    token: str,
    envelope: Mapping[str, Any],
    *,
    timeout_seconds: float = 30.0,
    ssl_context: ssl.SSLContext | None = None,
) -> SubmissionReceipt:
    """Upload one validated submission with a bearer enrollment token."""

    normalized_url = _validate_server_url(server_url)
    normalized_token = token.strip() if isinstance(token, str) else ""
    if not normalized_token:
        raise SubmissionError("submission token is empty")
    validated = validate_submission(envelope)
    body = _canonical_json_bytes(validated)
    endpoint = normalized_url + "/api/v1/submissions"
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {normalized_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"loopmetry/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
            context=ssl_context,
        ) as response:
            response_body = response.read(1_000_000)
    except urllib.error.HTTPError as exc:
        detail = exc.read(16_384).decode("utf-8", errors="replace").strip()
        try:
            parsed_detail = json.loads(detail)
            detail = str(parsed_detail.get("error") or parsed_detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        raise SubmissionError(f"submission server returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SubmissionError(f"cannot reach submission server: {exc}") from exc

    try:
        response_value = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SubmissionError("submission server returned invalid JSON") from exc
    if not isinstance(response_value, Mapping):
        raise SubmissionError("submission server returned an invalid receipt")
    return SubmissionReceipt.from_mapping(response_value)


def token_from_environment(name: str = DEFAULT_SUBMISSION_TOKEN_ENV) -> str:
    token = os.environ.get(name, "")
    if not token.strip():
        raise SubmissionError(
            f"environment variable {name} is required for submission upload"
        )
    return token.strip()

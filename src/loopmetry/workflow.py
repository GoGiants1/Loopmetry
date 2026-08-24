"""One-command participant analysis and submission workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence
from uuid import uuid4

from .event_merge import EventConflictError, merge_events
from .evaluation import ProjectEvaluator
from .evaluation_models import ProjectReport
from .io import InputError, load_jsonl, select_project
from .report import render
from .schema import Event
from .submission import (
    SubmissionError,
    SubmissionReceipt,
    build_submission,
    render_submission,
    submit_envelope,
    write_private_text,
)


@dataclass(frozen=True, slots=True)
class RunArtifacts:
    run_id: str
    run_directory: Path
    report_json: Path
    report_html: Path
    submission_json: Path
    manifest_json: Path
    report: ProjectReport
    submission: dict[str, object]
    receipt: SubmissionReceipt | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_id(created_at: datetime) -> str:
    timestamp = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{timestamp}-{uuid4().hex[:8]}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def discover_event_files(root: str | Path = ".") -> list[Path]:
    """Discover normalized event files produced by Loopmetry capture hooks.

    Discovery is intentionally narrow.  It never scans Claude Code or Codex home
    directories and never guesses vendor transcript formats.
    """

    base = Path(root).expanduser()
    candidates: set[Path] = set()
    for directory in (
        base / ".loopmetry" / "hooks",
        base / ".loopmetry" / "events",
    ):
        if directory.is_dir():
            candidates.update(path for path in directory.glob("*.jsonl") if path.is_file())
    return sorted(candidates, key=lambda path: str(path))


def load_event_files(paths: Iterable[str | Path]) -> list[Event]:
    materialized = [Path(path).expanduser() for path in paths]
    if not materialized:
        raise InputError(
            "no normalized event files were found; pass --input or configure Loopmetry hooks"
        )

    by_id: dict[str, Event] = {}
    origin: dict[str, Path] = {}
    for path in materialized:
        for event in load_jsonl(path):
            existing = by_id.get(event.event_id)
            if existing is None:
                by_id[event.event_id] = event
                origin[event.event_id] = path
                continue
            try:
                by_id[event.event_id] = merge_events(existing, event)
            except EventConflictError as exc:
                raise InputError(
                    "conflicting duplicate event_id "
                    f"{event.event_id!r} in {origin[event.event_id]} and {path}"
                ) from exc
    return sorted(by_id.values(), key=lambda event: (event.timestamp, event.event_id))


def _write_run_manifest(
    path: Path,
    *,
    run_id: str,
    project_id: str,
    assignment_id: str,
    submitter_id: str,
    source_files: Sequence[Path],
    receipt: SubmissionReceipt | None,
) -> None:
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "project_id": project_id,
        "assignment_id": assignment_id,
        "submitter_id": submitter_id,
        "source_files": [
            {
                "name": source.name,
                "sha256": _sha256_file(source),
            }
            for source in source_files
        ],
        "receipt": receipt.to_mapping() if receipt else None,
    }
    write_private_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def run_participant_workflow(
    source_files: Sequence[str | Path],
    *,
    assignment_id: str,
    submitter_id: str,
    project_id: str | None = None,
    output_root: str | Path = ".loopmetry/runs",
    server_url: str | None = None,
    submission_token: str | None = None,
    timeout_seconds: float = 30.0,
) -> RunArtifacts:
    """Analyze, render, package, and optionally upload one participant run."""

    normalized_sources = [Path(path).expanduser() for path in source_files]
    events = select_project(load_event_files(normalized_sources), project_id)
    report = ProjectEvaluator().evaluate(events)
    created_at = _utc_now()
    run_id = _run_id(created_at)
    run_directory = Path(output_root).expanduser() / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    try:
        run_directory.chmod(0o700)
    except OSError:
        pass

    report_json = write_private_text(run_directory / "report.json", render(report, "json") + "\n")
    report_html = write_private_text(run_directory / "report.html", render(report, "html"))
    submission = build_submission(
        report,
        events,
        assignment_id=assignment_id,
        submitter_id=submitter_id,
        source_file_count=len(normalized_sources),
        created_at=created_at,
        run_id=run_id,
    )
    submission_json = write_private_text(
        run_directory / "submission.json",
        render_submission(submission),
    )

    receipt: SubmissionReceipt | None = None
    manifest_json = run_directory / "manifest.json"
    _write_run_manifest(
        manifest_json,
        run_id=run_id,
        project_id=report.project_id,
        assignment_id=assignment_id,
        submitter_id=submitter_id,
        source_files=normalized_sources,
        receipt=None,
    )

    if server_url is not None:
        if not submission_token:
            raise SubmissionError("submission_token is required when server_url is set")
        receipt = submit_envelope(
            server_url,
            submission_token,
            submission,
            timeout_seconds=timeout_seconds,
        )
        write_private_text(
            run_directory / "receipt.json",
            json.dumps(receipt.to_mapping(), ensure_ascii=False, indent=2) + "\n",
        )
        _write_run_manifest(
            manifest_json,
            run_id=run_id,
            project_id=report.project_id,
            assignment_id=assignment_id,
            submitter_id=submitter_id,
            source_files=normalized_sources,
            receipt=receipt,
        )
    return RunArtifacts(
        run_id=run_id,
        run_directory=run_directory,
        report_json=report_json,
        report_html=report_html,
        submission_json=submission_json,
        manifest_json=manifest_json,
        report=report,
        submission=submission,
        receipt=receipt,
    )

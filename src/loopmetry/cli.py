"""Command-line interface for Loopmetry."""

from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .adapters.base import AdapterError, DiscoveryContext
from .adapters.checkpoints import atomic_write_bytes, load_checkpoint, save_checkpoint
from .adapters.claude_code_history import ClaudeCodeHistoryAdapter
from .admin_server import (
    DEFAULT_ADMIN_BIND,
    DEFAULT_ADMIN_PORT,
    DEFAULT_ADMIN_TOKEN_ENV,
    AdminServerError,
    create_admin_server,
)
from .admin_storage import AdminStorageError, AdminStore, REVIEW_STATUSES
from .event_merge import EventConflictError, merge_events
from .evaluation import ProjectEvaluator
from .hook_capture import (
    HookCaptureError,
    append_events,
    default_capture_path,
    normalize_hook_payload,
)
from .hook_integration import format_settings, merge_settings, remove_settings
from .io import InputError, load_jsonl, select_project
from .llm_bundle import BundleError, build_evaluation_bundle, render_evaluation_bundle
from .llm_provider import DEFAULT_API_KEY_ENV, DEFAULT_MODEL, ProviderError, evaluate
from .report import render
from .schema import Event
from .storage import EventStore
from .submission import (
    DEFAULT_SUBMISSION_TOKEN_ENV,
    SubmissionError,
    load_submission,
    submit_envelope,
    token_from_environment,
    write_private_text,
)
from .workflow import discover_event_files, run_participant_workflow

DEFAULT_CLAUDE_HOME_ENV = "LOOPMETRY_CLAUDE_HOME"
_HISTORY_ADAPTERS: dict[str, type] = {"claude-code": ClaudeCodeHistoryAdapter}

DEFAULT_DB = Path(".loopmetry/loopmetry.db")
DEFAULT_ADMIN_DB = Path(".loopmetry/admin.db")
DEFAULT_SERVER_ENV = "LOOPMETRY_SERVER_URL"
DEFAULT_ASSIGNMENT_ENV = "LOOPMETRY_ASSIGNMENT_ID"
DEFAULT_SUBMITTER_ENV = "LOOPMETRY_SUBMITTER_ID"
DEFAULT_UVX_SOURCE = "git+https://github.com/GoGiants1/Loopmetry.git"


def _write_output(content: str, output: str | None) -> None:
    if output is None or output == "-":
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        return
    output_path = Path(output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"wrote {output_path}")


def _read_json_object_from_stdin() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise HookCaptureError("hook capture expects one JSON object on stdin")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HookCaptureError(f"hook payload is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise HookCaptureError("hook payload must be a JSON object")
    return value


def _participant_source_files(args: argparse.Namespace) -> list[Path]:
    if args.input:
        return [Path(path).expanduser() for path in args.input]
    discovered = discover_event_files(args.root)
    if not discovered:
        raise InputError(
            f"no Loopmetry event files found below {Path(args.root).expanduser()}; "
            "pass --input or configure capture hooks"
        )
    return discovered


def _required_run_identity(args: argparse.Namespace) -> tuple[str, str]:
    assignment_id = (args.assignment_id or "").strip()
    submitter_id = (args.submitter_id or "").strip()
    if not assignment_id:
        raise SubmissionError(
            f"--assignment-id or environment variable {DEFAULT_ASSIGNMENT_ENV} is required"
        )
    if not submitter_id:
        raise SubmissionError(
            f"--submitter-id or environment variable {DEFAULT_SUBMITTER_ENV} is required"
        )
    return assignment_id, submitter_id


def _read_roster(path: str | Path) -> list[tuple[str, str]]:
    roster_path = Path(path).expanduser()
    try:
        with roster_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "submitter_id" not in reader.fieldnames:
                raise AdminStorageError("roster CSV requires a submitter_id column")
            rows: list[tuple[str, str]] = []
            for line_number, row in enumerate(reader, start=2):
                submitter_id = (row.get("submitter_id") or "").strip()
                display_name = (row.get("display_name") or "").strip()
                if not submitter_id:
                    raise AdminStorageError(
                        f"roster CSV line {line_number} has an empty submitter_id"
                    )
                rows.append((submitter_id, display_name))
            return rows
    except OSError as exc:
        raise AdminStorageError(f"cannot read roster CSV {roster_path}: {exc}") from exc



def _quote_powershell(value: str) -> str:
    """Quote one argument for a copy/paste PowerShell command."""

    if value and all(character.isalnum() or character in "-._/:+@" for character in value):
        return value
    return "'" + value.replace("'", "''") + "'"

def _credentials_csv(
    enrollments: Sequence[Any],
    *,
    server_url: str | None,
) -> str:
    buffer = io.StringIO(newline="")
    fields = [
        "assignment_id",
        "submitter_id",
        "display_name",
        "submission_token",
        "run_command",
        "run_command_powershell",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for enrollment in enrollments:
        command = ""
        powershell_command = ""
        if server_url:
            runner = [
                "uvx",
                "--from",
                DEFAULT_UVX_SOURCE,
                "loopmetry",
                "run",
                "--assignment-id",
                enrollment.assignment_id,
                "--submitter-id",
                enrollment.submitter_id,
                "--server",
                server_url,
            ]
            command = " ".join(
                [
                    f"{DEFAULT_SUBMISSION_TOKEN_ENV}={shlex.quote(enrollment.token)}",
                    *(shlex.quote(part) for part in runner),
                ]
            )
            ps_token = enrollment.token.replace("'", "''")
            powershell_command = (
                f"$env:{DEFAULT_SUBMISSION_TOKEN_ENV}='{ps_token}'; "
                + " ".join(_quote_powershell(part) for part in runner)
            )
        writer.writerow(
            {
                "assignment_id": enrollment.assignment_id,
                "submitter_id": enrollment.submitter_id,
                "display_name": enrollment.display_name,
                "submission_token": enrollment.token,
                "run_command": command,
                "run_command_powershell": powershell_command,
            }
        )
    return buffer.getvalue()


def _export_csv(rows: Sequence[dict[str, object]]) -> str:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ["assignment_id", "submitter_id", "display_name", "state"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopmetry",
        description="Evidence-backed project evaluation and submission for AI coding workflows.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a normalized JSONL file.")
    validate.add_argument("input", help="Path to normalized JSONL events.")

    analyze = subparsers.add_parser(
        "analyze", help="Evaluate a normalized JSONL file without persisting it."
    )
    analyze.add_argument("input", help="Path to normalized JSONL events.")
    analyze.add_argument("--project-id", help="Project ID when the file contains multiple projects.")
    analyze.add_argument("--format", choices=("markdown", "json", "html"), default="markdown")
    analyze.add_argument("--output", help="Output path; use '-' or omit for stdout.")

    run = subparsers.add_parser(
        "run",
        help="One-command analysis, local report generation, and optional administrator upload.",
    )
    run.add_argument(
        "--input",
        action="append",
        default=[],
        help="Normalized JSONL input. Repeat to merge files; otherwise discover .loopmetry hooks.",
    )
    run.add_argument("--root", default=".", help="Project root used for event discovery.")
    run.add_argument("--project-id", help="Project ID when inputs contain multiple projects.")
    run.add_argument(
        "--assignment-id",
        default=os.environ.get(DEFAULT_ASSIGNMENT_ENV),
        help=f"Assignment ID; defaults to ${DEFAULT_ASSIGNMENT_ENV}.",
    )
    run.add_argument(
        "--submitter-id",
        default=os.environ.get(DEFAULT_SUBMITTER_ENV),
        help=f"Roster identity; defaults to ${DEFAULT_SUBMITTER_ENV}.",
    )
    run.add_argument(
        "--server",
        default=os.environ.get(DEFAULT_SERVER_ENV),
        help=f"Administrator base URL; defaults to ${DEFAULT_SERVER_ENV}. Omit for local-only.",
    )
    run.add_argument(
        "--token-env",
        default=DEFAULT_SUBMISSION_TOKEN_ENV,
        help="Environment variable containing the enrollment token.",
    )
    run.add_argument("--output-root", default=".loopmetry/runs")
    run.add_argument("--timeout", type=float, default=30.0, help="Upload timeout in seconds.")

    submit = subparsers.add_parser(
        "submit", help="Retry upload of an existing submission.json without re-running analysis."
    )
    submit.add_argument("input", help="Path to submission.json.")
    submit.add_argument(
        "--server",
        default=os.environ.get(DEFAULT_SERVER_ENV),
        help=f"Administrator base URL; defaults to ${DEFAULT_SERVER_ENV}.",
    )
    submit.add_argument("--token-env", default=DEFAULT_SUBMISSION_TOKEN_ENV)
    submit.add_argument("--timeout", type=float, default=30.0)
    submit.add_argument("--receipt", help="Optional receipt JSON output path.")

    bundle = subparsers.add_parser(
        "bundle",
        help="Build a bounded JSON payload for a future optional LLM evaluation provider.",
    )
    bundle.add_argument("input", help="Path to normalized JSONL events.")
    bundle.add_argument("--project-id", help="Project ID when the file contains multiple projects.")
    bundle.add_argument("--max-events", type=int, default=1_000)
    bundle.add_argument("--max-bytes", type=int, default=1_000_000)
    bundle.add_argument("--output", help="Output path; use '-' or omit for stdout.")

    judge = subparsers.add_parser(
        "judge",
        help="EXPERIMENTAL: send a bundle to a real Anthropic API-key-based LLM judge.",
    )
    judge.add_argument("input", help="Path to a bundle JSON file produced by `loopmetry bundle`.")
    judge.add_argument(
        "--rubric",
        default="rubrics/project-work-v1.md",
        help="Path to the rubric text file.",
    )
    judge.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    judge.add_argument("--model", default=DEFAULT_MODEL)
    judge.add_argument("--max-tokens", type=int, default=8000)
    judge.add_argument("--output", help="Output path; use '-' or omit for stdout.")
    judge.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt before calling the Anthropic API.",
    )

    capture = subparsers.add_parser(
        "capture-hook",
        help="Read one Claude Code or Codex hook payload from stdin and append safe events.",
    )
    capture.add_argument("--source", choices=("claude-code", "codex"), required=True)
    capture.add_argument("--project-id", help="Explicit project ID; otherwise derived from cwd.")
    capture.add_argument("--output", help="Defaults to <cwd>/.loopmetry/hooks/<source>.jsonl.")
    capture.add_argument("--verbose", action="store_true")

    ingest = subparsers.add_parser("ingest", help="Persist normalized events in SQLite.")
    ingest.add_argument("input", help="Path to normalized JSONL events.")
    ingest.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")

    report = subparsers.add_parser("report", help="Evaluate one project from SQLite.")
    report.add_argument("project_id", help="Project ID stored in SQLite.")
    report.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    report.add_argument("--format", choices=("markdown", "json", "html"), default="markdown")
    report.add_argument("--output", help="Output path; use '-' or omit for stdout.")

    projects = subparsers.add_parser("projects", help="List projects in SQLite.")
    projects.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")

    admin = subparsers.add_parser("admin", help="Manage roster, submissions, and dashboard.")
    admin_subparsers = admin.add_subparsers(dest="admin_command", required=True)

    enroll = admin_subparsers.add_parser("enroll", help="Enroll or rotate one participant token.")
    enroll.add_argument("--db", default=str(DEFAULT_ADMIN_DB))
    enroll.add_argument("--assignment-id", required=True)
    enroll.add_argument("--submitter-id", required=True)
    enroll.add_argument("--display-name", default="")
    enroll.add_argument("--rotate", action="store_true")
    enroll.add_argument("--format", choices=("text", "json"), default="text")

    import_roster = admin_subparsers.add_parser(
        "import-roster", help="Enroll a CSV roster and write one-time participant tokens."
    )
    import_roster.add_argument("input", help="CSV with submitter_id and optional display_name.")
    import_roster.add_argument("--db", default=str(DEFAULT_ADMIN_DB))
    import_roster.add_argument("--assignment-id", required=True)
    import_roster.add_argument("--output", required=True, help="Private credentials CSV output.")
    import_roster.add_argument("--server", help="Optional URL embedded in ready-to-run commands.")

    admin_list = admin_subparsers.add_parser("list", help="List roster and latest submission state.")
    admin_list.add_argument("--db", default=str(DEFAULT_ADMIN_DB))
    admin_list.add_argument("--assignment-id")
    admin_list.add_argument("--status", choices=("not_submitted", *REVIEW_STATUSES))
    admin_list.add_argument("--query")
    admin_list.add_argument("--format", choices=("text", "json"), default="text")

    set_status = admin_subparsers.add_parser("set-status", help="Update manual review state.")
    set_status.add_argument("submission_id")
    set_status.add_argument("status", choices=REVIEW_STATUSES)
    set_status.add_argument("--note", default="")
    set_status.add_argument("--db", default=str(DEFAULT_ADMIN_DB))

    export = admin_subparsers.add_parser("export", help="Export latest roster state as CSV.")
    export.add_argument("--db", default=str(DEFAULT_ADMIN_DB))
    export.add_argument("--assignment-id")
    export.add_argument("--output", required=True)

    serve = admin_subparsers.add_parser("serve", help="Run submission API and HTML dashboard.")
    serve.add_argument("--db", default=str(DEFAULT_ADMIN_DB))
    serve.add_argument("--bind", default=DEFAULT_ADMIN_BIND)
    serve.add_argument("--port", type=int, default=DEFAULT_ADMIN_PORT)
    serve.add_argument("--admin-token-env", default=DEFAULT_ADMIN_TOKEN_ENV)
    serve.add_argument("--max-submission-bytes", type=int, default=2_000_000)
    serve.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow a non-loopback bind. Put the server behind TLS and access control.",
    )

    history = subparsers.add_parser(
        "history",
        help="Discover, preview, and import existing local agent sessions (consented backfill).",
    )
    history_subparsers = history.add_subparsers(dest="history_command", required=True)
    for verb, help_text in (
        ("discover", "List importable sessions for this project."),
        ("preview", "Show what an import would read, without importing."),
        ("import", "Import sessions into canonical events (requires consent)."),
    ):
        verb_parser = history_subparsers.add_parser(verb, help=help_text)
        verb_parser.add_argument("--source", required=True, choices=sorted(_HISTORY_ADAPTERS))
        verb_parser.add_argument("--root", default=".")
        verb_parser.add_argument("--since", default=None, help="YYYY-MM-DD lower bound")
        if verb == "import":
            verb_parser.add_argument("--output", default=None)
            verb_parser.add_argument(
                "--yes",
                action="store_true",
                help="Consent to reading local Claude Code transcripts non-interactively.",
            )
        else:
            verb_parser.add_argument("--json", action="store_true")

    integrate = subparsers.add_parser(
        "integrate",
        help="Preview, apply, or remove local hook configuration for a capture source.",
    )
    integrate.add_argument("source", choices=("claude-code",))
    integrate.add_argument("--root", default=".")
    integrate.add_argument(
        "--project-id", default=None, help="Embed a fixed --project-id in the generated hook command."
    )
    integrate.add_argument(
        "--force", action="store_true", help="Allow apply/remove to modify an existing settings file."
    )
    integrate_mode = integrate.add_mutually_exclusive_group(required=True)
    integrate_mode.add_argument("--preview", action="store_true")
    integrate_mode.add_argument("--apply", action="store_true")
    integrate_mode.add_argument("--remove", action="store_true")

    return parser


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise InputError(f"--since must be YYYY-MM-DD, got {value!r}") from exc


def _write_events_atomically(path: Path, events: Sequence[Event]) -> None:
    payload = "".join(
        json.dumps(event.to_mapping(), ensure_ascii=False, separators=(",", ":")) + "\n"
        for event in events
    ).encode("utf-8")
    atomic_write_bytes(path, payload)


def _run_integrate(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    path = root / ".claude" / "settings.local.json"
    existing_text = path.read_text(encoding="utf-8") if path.is_file() else None
    if existing_text is None:
        existing: dict[str, Any] = {}
    else:
        try:
            existing = json.loads(existing_text)
        except json.JSONDecodeError as exc:
            raise InputError(
                f"{path}: existing file is not valid JSON; fix or remove it manually"
            ) from exc
        if not isinstance(existing, dict):
            raise InputError(f"{path}: existing file's top-level JSON value is not an object")

    if args.remove:
        merged, changed = remove_settings(existing)
    else:
        merged, changed = merge_settings(existing, args.project_id)

    old_text = existing_text or ""
    new_text = format_settings(merged) if changed else old_text

    if args.preview:
        if not changed:
            print("no changes needed")
        else:
            diff = difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=str(path) if existing_text is not None else "/dev/null",
                tofile=str(path),
            )
            sys.stdout.writelines(diff)
        return 0

    if not changed:
        print("no changes needed")
        return 0

    if existing_text is not None and not args.force:
        raise InputError(
            f"{path} already exists; pass --force to modify it "
            "(run with --preview first to review the diff)"
        )

    if existing_text is not None:
        backup_path = path.with_name(path.name + ".bak")
        atomic_write_bytes(backup_path, existing_text.encode("utf-8"))
        print(f"backed up existing file to {backup_path}")

    atomic_write_bytes(path, new_text.encode("utf-8"))
    print(f"{'updated' if existing_text is not None else 'created'} {path}")
    return 0


def _run_history(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    since = _parse_since(args.since)
    interactive = sys.stdin.isatty()
    context = DiscoveryContext(project_root=root, since=since, interactive=interactive)
    claude_home_raw = os.environ.get(DEFAULT_CLAUDE_HOME_ENV)
    claude_home = Path(claude_home_raw).expanduser() if claude_home_raw else None
    adapter = _HISTORY_ADAPTERS[args.source](claude_home=claude_home)

    if args.history_command == "discover":
        candidates = adapter.discover(context)
        if args.json:
            _write_output(
                json.dumps(
                    [
                        {
                            "label": c.label,
                            "session_id": c.session_id,
                            "size_bytes": c.size_bytes,
                            "modified_at": c.modified_at.isoformat().replace("+00:00", "Z"),
                        }
                        for c in candidates
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                None,
            )
        elif not candidates:
            print("no sessions found")
        else:
            for candidate in candidates:
                print(
                    f"{candidate.label}\t{candidate.session_id}\t{candidate.size_bytes}B\t"
                    f"{candidate.modified_at.isoformat().replace('+00:00', 'Z')}"
                )
        return 0

    if args.history_command == "preview":
        candidates = adapter.discover(context)
        preview = adapter.preview(candidates)
        diagnostics = adapter.last_discovery_diagnostics
        if args.json:
            _write_output(
                json.dumps(
                    {
                        "sessions": preview.session_count,
                        "total_size_bytes": preview.total_size_bytes,
                        "candidates": [
                            {
                                "label": c.label,
                                "session_id": c.session_id,
                                "size_bytes": c.size_bytes,
                            }
                            for c in preview.candidates
                        ],
                        "diagnostics": [
                            {"kind": d.kind, "summary": d.summary, "count": d.count}
                            for d in diagnostics
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                None,
            )
        else:
            if not preview.candidates:
                print("no sessions found")
            else:
                for candidate in preview.candidates:
                    print(f"{candidate.label}\t{candidate.session_id}\t{candidate.size_bytes}B")
                print(
                    f"total: {preview.session_count} session(s), "
                    f"{preview.total_size_bytes} byte(s)"
                )
            for diagnostic in diagnostics:
                print(f"diagnostic: {diagnostic.kind} ({diagnostic.count}): {diagnostic.summary}")
        return 0

    if args.history_command == "import":
        # Consent must be checked before any transcript content is read at all —
        # discover() itself opens and JSON-parses the first lines of every
        # candidate file to confirm its cwd, so calling it before this check
        # would mean a rejected non-interactive run had already read local
        # history. Interactive mode asks twice: once to scan at all, and again
        # (with real counts) before actually importing.
        if interactive:
            scan_answer = input(
                "Scan local Claude Code history for this project to preview "
                "importable sessions? [y/N] "
            ).strip().lower()
            if scan_answer != "y":
                print("import cancelled")
                return 0
        elif not args.yes:
            raise InputError(
                "loopmetry history import requires --yes when not run interactively "
                "(this flag is the explicit consent to read local history)"
            )

        candidates = adapter.discover(context)
        preview = adapter.preview(candidates)

        if interactive:
            print(
                f"{preview.session_count} session(s), {preview.total_size_bytes} byte(s) "
                "of local Claude Code history will be read."
            )
            answer = input("Proceed with import? [y/N] ").strip().lower()
            if answer != "y":
                print("import cancelled")
                return 0

        try:
            checkpoint = load_checkpoint(root, adapter.name)
        except AdapterError as exc:
            print(f"warning: {exc}; re-importing without a checkpoint", file=sys.stderr)
            checkpoint = None

        run = adapter.import_candidates(candidates, context, checkpoint=checkpoint)

        output_path = (
            Path(args.output).expanduser()
            if args.output
            else root / ".loopmetry" / "events" / "claude-code-history.jsonl"
        )
        # Fail closed: a corrupt or unparsable existing output file must never be
        # treated as "no prior evidence" and silently overwritten with only this
        # run's events (that would delete everything previously imported). The
        # checkpoint from this run has not been saved yet, so raising here leaves
        # both the output file and the checkpoint untouched — safe to retry once
        # the file is fixed or removed by hand.
        existing_events = load_jsonl(output_path) if output_path.exists() else []

        by_id: dict[str, Event] = {event.event_id: event for event in existing_events}
        for event in run.events:
            existing = by_id.get(event.event_id)
            if existing is None:
                by_id[event.event_id] = event
                continue
            # Overlapping observations merge without losing provenance (invariant
            # 10); a genuine content conflict under the same event_id is an error,
            # matching io.load_jsonl and EventStore.add_events elsewhere.
            try:
                by_id[event.event_id] = merge_events(existing, event)
            except EventConflictError as exc:
                raise InputError(
                    f"{output_path}: conflicting duplicate event_id {event.event_id!r}"
                ) from exc
        merged_events = sorted(by_id.values(), key=lambda event: (event.timestamp, event.event_id))
        _write_events_atomically(output_path, merged_events)

        if run.checkpoint is not None:
            save_checkpoint(root, run.checkpoint)

        new_count = len(by_id) - len(existing_events)
        diagnostic_summary = (
            ", ".join(f"{d.kind}={d.count}" for d in run.diagnostics) or "none"
        )
        print(
            f"imported {new_count} new event(s); {len(merged_events)} total in {output_path}"
        )
        print(f"diagnostics: {diagnostic_summary}")
        print(f"coverage: {run.coverage.to_mapping()['categories']}")
        return 0

    raise AssertionError(f"unhandled history command: {args.history_command}")


def _run_admin(args: argparse.Namespace) -> int:
    store = AdminStore(args.db)
    if args.admin_command == "enroll":
        enrollment = store.enroll(
            assignment_id=args.assignment_id,
            submitter_id=args.submitter_id,
            display_name=args.display_name,
            rotate=args.rotate,
        )
        if args.format == "json":
            _write_output(json.dumps(enrollment.to_mapping(), ensure_ascii=False, indent=2), None)
        else:
            print(f"assignment: {enrollment.assignment_id}")
            print(f"submitter: {enrollment.submitter_id}")
            print(f"submission token: {enrollment.token}")
            print("Store this token securely; only its hash is retained by the server.")
        return 0

    if args.admin_command == "import-roster":
        participants = _read_roster(args.input)
        enrollments = store.enroll_many(
            assignment_id=args.assignment_id,
            participants=participants,
        )
        content = _credentials_csv(enrollments, server_url=args.server)
        path = write_private_text(args.output, content)
        print(f"enrolled {len(enrollments)} participant(s); wrote private credentials to {path}")
        return 0

    if args.admin_command == "list":
        overview = store.list_overview(
            assignment_id=args.assignment_id,
            status=args.status,
            query=args.query,
        )
        if args.format == "json":
            value = [
                {
                    "assignment_id": item.participant.assignment_id,
                    "submitter_id": item.participant.submitter_id,
                    "display_name": item.participant.display_name,
                    "state": item.state,
                    "latest_submission": item.latest.summary_mapping() if item.latest else None,
                }
                for item in overview
            ]
            _write_output(json.dumps(value, ensure_ascii=False, indent=2), None)
        else:
            print("assignment\tsubmitter\tname\tstate\tattempt\tproject\treceived")
            for item in overview:
                latest = item.latest
                print(
                    "\t".join(
                        [
                            item.participant.assignment_id,
                            item.participant.submitter_id,
                            item.participant.display_name,
                            item.state,
                            str(latest.attempt) if latest else "",
                            latest.project_id if latest else "",
                            latest.received_at if latest else "",
                        ]
                    )
                )
        return 0

    if args.admin_command == "set-status":
        updated = store.update_status(args.submission_id, args.status, args.note)
        print(
            f"updated {updated.submission_id}: status={updated.status} "
            f"submitter={updated.submitter_id} attempt={updated.attempt}"
        )
        return 0

    if args.admin_command == "export":
        rows = store.export_rows(assignment_id=args.assignment_id)
        path = write_private_text(args.output, _export_csv(rows))
        print(f"exported {len(rows)} roster row(s) to {path}")
        return 0

    if args.admin_command == "serve":
        loopback_names = {"127.0.0.1", "localhost", "::1"}
        if args.bind not in loopback_names and not args.allow_remote:
            raise AdminServerError(
                "non-loopback bind requires --allow-remote and a TLS/authenticated reverse proxy"
            )
        admin_token = os.environ.get(args.admin_token_env, "").strip()
        if not admin_token:
            raise AdminServerError(
                f"environment variable {args.admin_token_env} is required"
            )
        server = create_admin_server(
            database=args.db,
            admin_token=admin_token,
            bind=args.bind,
            port=args.port,
            max_submission_bytes=args.max_submission_bytes,
        )
        host, port = server.server_address[:2]
        print(f"Loopmetry admin server listening on http://{host}:{port}")
        print("Dashboard username: admin")
        print(f"Dashboard password: value of ${args.admin_token_env}")
        print("Remote participant uploads require HTTPS termination in front of this server.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nshutting down")
        finally:
            server.server_close()
        return 0

    raise AssertionError(f"unhandled admin command: {args.admin_command}")


def _run(args: argparse.Namespace) -> int:
    if args.command == "validate":
        events = load_jsonl(args.input)
        projects = sorted({event.project_id for event in events})
        print(f"valid: {len(events)} event(s), {len(projects)} project(s)")
        return 0

    if args.command == "analyze":
        events = select_project(load_jsonl(args.input), args.project_id)
        report = ProjectEvaluator().evaluate(events)
        _write_output(render(report, args.format), args.output)
        return 0

    if args.command == "run":
        assignment_id, submitter_id = _required_run_identity(args)
        source_files = _participant_source_files(args)
        token = token_from_environment(args.token_env) if args.server else None
        artifacts = run_participant_workflow(
            source_files,
            assignment_id=assignment_id,
            submitter_id=submitter_id,
            project_id=args.project_id,
            output_root=args.output_root,
            server_url=args.server,
            submission_token=token,
            timeout_seconds=args.timeout,
        )
        print(f"analysis complete: project={artifacts.report.project_id} run={artifacts.run_id}")
        print(f"HTML report: {artifacts.report_html}")
        print(f"submission file: {artifacts.submission_json}")
        if artifacts.receipt:
            duplicate = " duplicate" if artifacts.receipt.duplicate else ""
            print(
                f"uploaded:{duplicate} submission={artifacts.receipt.submission_id} "
                f"attempt={artifacts.receipt.attempt} status={artifacts.receipt.status}"
            )
        else:
            print("upload skipped: no --server was configured")
        return 0

    if args.command == "submit":
        if not args.server:
            raise SubmissionError(
                f"--server or environment variable {DEFAULT_SERVER_ENV} is required"
            )
        envelope = load_submission(args.input)
        token = token_from_environment(args.token_env)
        receipt = submit_envelope(
            args.server,
            token,
            envelope,
            timeout_seconds=args.timeout,
        )
        rendered = json.dumps(receipt.to_mapping(), ensure_ascii=False, indent=2) + "\n"
        if args.receipt:
            write_private_text(args.receipt, rendered)
            print(f"wrote receipt {args.receipt}")
        else:
            _write_output(rendered, None)
        return 0

    if args.command == "bundle":
        events = select_project(load_jsonl(args.input), args.project_id)
        bundle = build_evaluation_bundle(
            events,
            max_events=args.max_events,
            max_bytes=args.max_bytes,
        )
        _write_output(render_evaluation_bundle(bundle), args.output)
        return 0

    if args.command == "judge":
        bundle = json.loads(Path(args.input).read_text(encoding="utf-8"))
        rubric_text = Path(args.rubric).read_text(encoding="utf-8")
        rubric_id = Path(args.rubric).stem
        bundle_id = bundle.get("bundle_id", "unknown")
        event_count = bundle.get("source_coverage", {}).get("event_count", "unknown")
        print(
            f"about to send bundle {bundle_id} "
            f"(project={bundle.get('project_id', 'unknown')}, events={event_count}) "
            f"to model {args.model} using ${args.api_key_env}",
            file=sys.stderr,
        )
        if not args.yes:
            reply = input("Continue? [y/N] ").strip().lower()
            if reply not in ("y", "yes"):
                print("aborted: pass --yes to skip this prompt", file=sys.stderr)
                return 1
        outcome = evaluate(
            bundle,
            rubric_text,
            model=args.model,
            api_key_env=args.api_key_env,
            max_tokens=args.max_tokens,
            rubric_id=rubric_id,
        )
        output = {
            "judge_run": {
                "provider": "anthropic",
                "model": outcome["model"],
                "bundle_id": bundle_id,
                "rubric_id": rubric_id,
                "requested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "usage": outcome["usage"],
            },
            "result": outcome["result"],
        }
        _write_output(json.dumps(output, ensure_ascii=False, indent=2), args.output)
        return 0

    if args.command == "capture-hook":
        payload = _read_json_object_from_stdin()
        events = normalize_hook_payload(
            payload,
            source=args.source,
            project_id=args.project_id,
        )
        destination = (
            Path(args.output).expanduser()
            if args.output
            else default_capture_path(payload, source=args.source)
        )
        count = append_events(destination, events)
        if args.verbose:
            print(f"captured {count} event(s) -> {destination}", file=sys.stderr)
        return 0

    if args.command == "ingest":
        events = load_jsonl(args.input)
        with EventStore(args.db) as store:
            result = store.add_events(events)
        print(
            f"ingested: {result.inserted} inserted, {result.merged} merged, "
            f"{result.skipped} duplicate(s) skipped"
        )
        return 0

    if args.command == "report":
        with EventStore(args.db) as store:
            events = store.list_events(args.project_id)
        if not events:
            raise InputError(f"project not found in database: {args.project_id}")
        report = ProjectEvaluator().evaluate(events)
        _write_output(render(report, args.format), args.output)
        return 0

    if args.command == "projects":
        with EventStore(args.db) as store:
            projects = store.list_projects()
        if not projects:
            print("no projects")
            return 0
        for project_id, event_count in projects:
            print(f"{project_id}\t{event_count}")
        return 0

    if args.command == "admin":
        return _run_admin(args)

    if args.command == "history":
        return _run_history(args)

    if args.command == "integrate":
        return _run_integrate(args)

    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (
        AdminServerError,
        AdminStorageError,
        BundleError,
        HookCaptureError,
        InputError,
        OSError,
        ProviderError,
        SubmissionError,
        ValueError,
    ) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

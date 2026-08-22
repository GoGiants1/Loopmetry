"""Command-line interface for Loopmetry."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .evaluation import ProjectEvaluator
from .io import InputError, load_jsonl, select_project
from .report import render
from .storage import EventStore

DEFAULT_DB = Path(".loopmetry/loopmetry.db")


def _write_output(content: str, output: str | None) -> None:
    if output is None or output == "-":
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        return
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"wrote {output_path}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopmetry",
        description="Local-first project evidence evaluation for AI coding workflows.",
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
    analyze.add_argument(
        "--format", choices=("markdown", "json", "html"), default="markdown"
    )
    analyze.add_argument("--output", help="Output path; use '-' or omit for stdout.")

    ingest = subparsers.add_parser("ingest", help="Persist normalized events in SQLite.")
    ingest.add_argument("input", help="Path to normalized JSONL events.")
    ingest.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")

    report = subparsers.add_parser("report", help="Evaluate one project from SQLite.")
    report.add_argument("project_id", help="Project ID stored in SQLite.")
    report.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    report.add_argument(
        "--format", choices=("markdown", "json", "html"), default="markdown"
    )
    report.add_argument("--output", help="Output path; use '-' or omit for stdout.")

    projects = subparsers.add_parser("projects", help="List projects in SQLite.")
    projects.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    return parser


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

    if args.command == "ingest":
        events = load_jsonl(args.input)
        with EventStore(args.db) as store:
            result = store.add_events(events)
        print(
            f"ingested: {result.inserted} inserted, {result.skipped} duplicate(s) skipped"
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

    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (InputError, OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

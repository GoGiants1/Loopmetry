"""Render Loopmetry reports as JSON or Markdown."""

from __future__ import annotations

import json
from typing import Iterable

from .evaluation import MetricResult, ProjectReport


def render_json(report: ProjectReport) -> str:
    return json.dumps(report.to_mapping(), ensure_ascii=False, indent=2, sort_keys=False)


def _format_component_name(name: str) -> str:
    return name.replace("_", " ").title()


def _render_metric(metric: MetricResult) -> list[str]:
    lines = [
        f"### {metric.title} — {metric.score:.1f}/100",
        "",
        f"Confidence: **{metric.confidence:.2f}**",
        "",
        metric.summary,
        "",
        "| Component | Score |",
        "|---|---:|",
    ]
    for name, score in metric.components.items():
        lines.append(f"| {_format_component_name(name)} | {score:.1f} |")

    if metric.evidence:
        lines.extend(["", "Evidence:"])
        for evidence in metric.evidence:
            timestamp = evidence.timestamp.isoformat().replace("+00:00", "Z")
            lines.append(
                f"- `{evidence.event_id}` · {timestamp} · {evidence.event_type}: "
                f"{evidence.summary}"
            )
    if metric.gaps:
        lines.extend(["", "Measurement gaps:"])
        lines.extend(f"- {gap}" for gap in metric.gaps)
    return lines


def render_markdown(report: ProjectReport) -> str:
    snapshot = report.snapshot
    lines = [
        f"# Loopmetry project report: `{report.project_id}`",
        "",
        "> Loopmetry evaluates recorded project evidence. It does not score developer ability, "
        "employment suitability, or individual productivity.",
        "",
        "## Snapshot",
        "",
        "| Signal | Value |",
        "|---|---:|",
        f"| Events | {snapshot.event_count} |",
        f"| Sessions | {snapshot.session_count} |",
        f"| Requirements | {snapshot.requirement_count} |",
        f"| Changed files | {snapshot.changed_file_count} |",
        f"| Verifications | {snapshot.verification_count} |",
        f"| Errors | {snapshot.error_count} |",
        f"| Commits | {snapshot.commit_count} |",
        "",
        "## Metric cards",
        "",
        "No overall rank is produced. Each metric has its own evidence and confidence.",
        "",
    ]
    for index, metric in enumerate(report.metrics):
        if index:
            lines.extend(["", "---", ""])
        lines.extend(_render_metric(metric))

    lines.extend(
        [
            "",
            "## Non-scored workflow signal",
            "",
            f"**Steering style: `{report.steering.label}`** "
            f"(confidence {report.steering.confidence:.2f})",
            "",
            report.steering.summary,
        ]
    )
    if report.measurement_gaps:
        lines.extend(["", "## Project-wide measurement gaps", ""])
        lines.extend(f"- {gap}" for gap in report.measurement_gaps)

    generated_at = report.generated_at.isoformat().replace("+00:00", "Z")
    lines.extend(["", f"Generated at `{generated_at}`.", ""])
    return "\n".join(lines)


def render(report: ProjectReport, output_format: str) -> str:
    normalized = output_format.strip().lower()
    if normalized == "json":
        return render_json(report)
    if normalized in {"md", "markdown"}:
        return render_markdown(report)
    raise ValueError(f"unsupported output format: {output_format}")

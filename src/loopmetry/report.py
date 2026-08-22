"""Render Loopmetry reports as JSON, Markdown, or standalone HTML."""

from __future__ import annotations

import html
import json
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


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _score_tone(score: float) -> str:
    if score >= 80:
        return "strong"
    if score >= 60:
        return "moderate"
    return "weak"


def _render_html_metric(metric: MetricResult) -> str:
    component_rows = "".join(
        "<tr>"
        f"<td>{_escape(_format_component_name(name))}</td>"
        f"<td class=\"number\">{score:.1f}</td>"
        "</tr>"
        for name, score in metric.components.items()
    )

    evidence_html = ""
    if metric.evidence:
        evidence_items = "".join(
            "<li>"
            f"<code>{_escape(item.event_id)}</code>"
            f"<span>{_escape(item.timestamp.isoformat().replace('+00:00', 'Z'))}</span>"
            f"<span class=\"pill\">{_escape(item.event_type)}</span>"
            f"<p>{_escape(item.summary)}</p>"
            "</li>"
            for item in metric.evidence
        )
        evidence_html = (
            "<details>"
            f"<summary>Evidence <span>{len(metric.evidence)}</span></summary>"
            f"<ol class=\"evidence-list\">{evidence_items}</ol>"
            "</details>"
        )

    gaps_html = ""
    if metric.gaps:
        items = "".join(f"<li>{_escape(gap)}</li>" for gap in metric.gaps)
        gaps_html = f"<div class=\"gaps\"><h4>Measurement gaps</h4><ul>{items}</ul></div>"

    return (
        "<article class=\"metric-card\">"
        "<header class=\"metric-header\">"
        "<div>"
        f"<p class=\"eyebrow\">{_escape(metric.key)}</p>"
        f"<h3>{_escape(metric.title)}</h3>"
        "</div>"
        f"<div class=\"score { _score_tone(metric.score) }\">"
        f"<strong>{metric.score:.1f}</strong><span>/100</span>"
        "</div>"
        "</header>"
        f"<div class=\"bar\"><span style=\"width:{max(0.0, min(100.0, metric.score)):.1f}%\"></span></div>"
        f"<p class=\"confidence\">Confidence <strong>{metric.confidence:.2f}</strong></p>"
        f"<p class=\"summary\">{_escape(metric.summary)}</p>"
        "<table><thead><tr><th>Component</th><th>Score</th></tr></thead>"
        f"<tbody>{component_rows}</tbody></table>"
        f"{evidence_html}{gaps_html}"
        "</article>"
    )


def render_html(report: ProjectReport) -> str:
    """Render a dependency-free, self-contained HTML project report."""

    snapshot = report.snapshot
    snapshot_items = (
        ("Events", snapshot.event_count),
        ("Sessions", snapshot.session_count),
        ("Requirements", snapshot.requirement_count),
        ("Changed files", snapshot.changed_file_count),
        ("Verifications", snapshot.verification_count),
        ("Errors", snapshot.error_count),
        ("Commits", snapshot.commit_count),
    )
    snapshot_html = "".join(
        f"<div class=\"snapshot-card\"><span>{_escape(label)}</span><strong>{value}</strong></div>"
        for label, value in snapshot_items
    )
    metrics_html = "".join(_render_html_metric(metric) for metric in report.metrics)

    project_gaps = ""
    if report.measurement_gaps:
        items = "".join(f"<li>{_escape(gap)}</li>" for gap in report.measurement_gaps)
        project_gaps = (
            "<section class=\"panel\"><h2>Project-wide measurement gaps</h2>"
            f"<ul>{items}</ul></section>"
        )

    generated_at = report.generated_at.isoformat().replace("+00:00", "Z")
    started_at = snapshot.started_at.isoformat().replace("+00:00", "Z")
    ended_at = snapshot.ended_at.isoformat().replace("+00:00", "Z")
    title = f"Loopmetry report — {report.project_id}"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{_escape(title)}</title>
<style>
:root {{
  --background: #f7f7f5;
  --surface: #ffffff;
  --text: #20201f;
  --muted: #6d6c68;
  --line: #deddd8;
  --accent: #e86f28;
  --accent-soft: #fff0e7;
  --strong: #2f7864;
  --moderate: #9a6a1e;
  --weak: #a44a45;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--background); color: var(--text); line-height: 1.55; }}
main {{ width: min(1160px, calc(100% - 40px)); margin: 0 auto; padding: 48px 0 72px; }}
.hero {{ display: grid; gap: 24px; grid-template-columns: 1.6fr 1fr; align-items: end; margin-bottom: 32px; }}
.brand {{ color: var(--accent); font-weight: 750; letter-spacing: .08em; text-transform: uppercase; margin: 0 0 8px; }}
h1 {{ font-size: clamp(2rem, 5vw, 4rem); letter-spacing: -.04em; line-height: 1.02; margin: 0; }}
h2 {{ font-size: 1.4rem; margin: 0 0 16px; }}
h3 {{ font-size: 1.22rem; margin: 0; }}
.meta {{ color: var(--muted); font-size: .92rem; border-left: 3px solid var(--accent); padding-left: 16px; }}
.meta p {{ margin: 4px 0; }}
.notice {{ background: var(--accent-soft); border: 1px solid #f4cdb6; border-radius: 14px; padding: 16px 18px; margin: 0 0 24px; }}
.snapshot {{ display: grid; grid-template-columns: repeat(7, minmax(100px, 1fr)); gap: 10px; margin-bottom: 28px; }}
.snapshot-card, .panel, .metric-card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 16px; }}
.snapshot-card {{ padding: 14px; min-height: 94px; display: flex; flex-direction: column; justify-content: space-between; }}
.snapshot-card span {{ color: var(--muted); font-size: .8rem; }}
.snapshot-card strong {{ font-size: 1.75rem; }}
.section-heading {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin: 36px 0 14px; }}
.section-heading p {{ margin: 0; color: var(--muted); }}
.metrics {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }}
.metric-card {{ padding: 22px; min-width: 0; }}
.metric-header {{ display: flex; justify-content: space-between; gap: 18px; align-items: start; }}
.eyebrow {{ color: var(--muted); font: 700 .72rem/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .07em; margin: 0 0 6px; text-transform: uppercase; }}
.score {{ display: flex; align-items: baseline; gap: 3px; white-space: nowrap; }}
.score strong {{ font-size: 1.9rem; }}
.score span {{ color: var(--muted); font-size: .8rem; }}
.score.strong strong {{ color: var(--strong); }}
.score.moderate strong {{ color: var(--moderate); }}
.score.weak strong {{ color: var(--weak); }}
.bar {{ height: 7px; background: #ecebe7; border-radius: 999px; overflow: hidden; margin: 18px 0 8px; }}
.bar span {{ display: block; height: 100%; background: var(--accent); border-radius: inherit; }}
.confidence {{ color: var(--muted); font-size: .83rem; margin: 0 0 16px; }}
.summary {{ min-height: 3.2em; }}
table {{ width: 100%; border-collapse: collapse; margin: 18px 0; font-size: .9rem; }}
th, td {{ padding: 8px 0; border-bottom: 1px solid var(--line); text-align: left; }}
th {{ color: var(--muted); font-size: .75rem; text-transform: uppercase; letter-spacing: .05em; }}
.number {{ text-align: right; font-variant-numeric: tabular-nums; }}
details {{ border-top: 1px solid var(--line); padding-top: 14px; margin-top: 16px; }}
summary {{ cursor: pointer; font-weight: 650; }}
summary span {{ color: var(--muted); font-weight: 500; }}
.evidence-list {{ padding-left: 22px; }}
.evidence-list li {{ margin: 12px 0; }}
.evidence-list span {{ color: var(--muted); font-size: .8rem; margin-left: 8px; }}
.evidence-list p {{ margin: 4px 0 0; }}
code, .pill {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
.pill {{ background: #f0efeb; border-radius: 999px; padding: 2px 7px; }}
.gaps {{ background: #fbf7ef; border-radius: 12px; padding: 13px 15px; margin-top: 16px; }}
.gaps h4 {{ margin: 0 0 6px; }}
.gaps ul, .panel ul {{ margin: 6px 0; padding-left: 20px; }}
.panel {{ padding: 22px; margin-top: 18px; }}
.steering {{ display: flex; justify-content: space-between; gap: 24px; align-items: start; }}
.steering .label {{ display: inline-block; color: var(--accent); font: 700 .82rem ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--accent-soft); border-radius: 999px; padding: 6px 10px; }}
footer {{ color: var(--muted); font-size: .82rem; margin-top: 30px; }}
@media (max-width: 900px) {{
  .hero, .metrics {{ grid-template-columns: 1fr; }}
  .snapshot {{ grid-template-columns: repeat(3, 1fr); }}
}}
@media (max-width: 560px) {{
  main {{ width: min(100% - 24px, 1160px); padding-top: 28px; }}
  .snapshot {{ grid-template-columns: repeat(2, 1fr); }}
  .section-heading, .steering {{ display: block; }}
}}
</style>
</head>
<body>
<main>
  <header class="hero">
    <div>
      <p class="brand">Loopmetry</p>
      <h1>{_escape(report.project_id)}</h1>
    </div>
    <div class="meta">
      <p><strong>Evidence window</strong></p>
      <p>{_escape(started_at)} → {_escape(ended_at)}</p>
      <p>Generated {_escape(generated_at)}</p>
    </div>
  </header>
  <p class="notice">Loopmetry evaluates recorded project evidence. It does not score developer ability, employment suitability, or individual productivity.</p>
  <section class="snapshot" aria-label="Project snapshot">{snapshot_html}</section>
  <div class="section-heading">
    <h2>Metric cards</h2>
    <p>No overall rank is produced.</p>
  </div>
  <section class="metrics">{metrics_html}</section>
  <section class="panel steering">
    <div>
      <h2>Non-scored workflow signal</h2>
      <span class="label">{_escape(report.steering.label)}</span>
    </div>
    <div>
      <p><strong>Confidence {report.steering.confidence:.2f}</strong></p>
      <p>{_escape(report.steering.summary)}</p>
    </div>
  </section>
  {project_gaps}
  <footer>Standalone local report. No external scripts, fonts, analytics, or network requests are used.</footer>
</main>
</body>
</html>
"""


def render(report: ProjectReport, output_format: str) -> str:
    normalized = output_format.strip().lower()
    if normalized == "json":
        return render_json(report)
    if normalized in {"md", "markdown"}:
        return render_markdown(report)
    if normalized in {"html", "htm"}:
        return render_html(report)
    raise ValueError(f"unsupported output format: {output_format}")

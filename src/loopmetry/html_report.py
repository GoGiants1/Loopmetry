"""Self-contained HTML renderer for Loopmetry project reports."""

from __future__ import annotations

from html import escape
import json
from typing import Any

from .evaluation import MetricResult, ProjectReport
from .html_assets import REPORT_CSS


def _format_component_name(name: str) -> str:
    return name.replace("_", " ").title()


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _safe_embedded_json(value: Any) -> str:
    """Serialize JSON without allowing it to terminate the embedding script."""

    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _progress(value: float, *, label: str, compact: bool = False) -> str:
    score = _clamp_score(value)
    compact_class = " progress--compact" if compact else ""
    return (
        f'<div class="progress{compact_class}" role="img" '
        f'aria-label="{escape(label, quote=True)}: {score:.1f} out of 100">'
        f'<span style="width:{score:.1f}%"></span></div>'
    )


def _metric_card(metric: MetricResult) -> str:
    components = "".join(
        '<div class="component">'
        f'<div class="component__label">{escape(_format_component_name(name))}</div>'
        f'{_progress(score, label=_format_component_name(name), compact=True)}'
        f'<div class="component__value">{score:.1f}</div>'
        "</div>"
        for name, score in metric.components.items()
    )
    evidence = "".join(
        '<li class="evidence-item"><div class="evidence-item__meta">'
        f'<code>{escape(item.event_id)}</code>'
        f'<span>{escape(item.timestamp.isoformat().replace("+00:00", "Z"))}</span>'
        f'<span>{escape(item.event_type)}</span></div>'
        f'<p>{escape(item.summary)}</p></li>'
        for item in metric.evidence
    ) or '<li class="empty-state">No evidence reference was emitted.</li>'
    gaps = "".join(f"<li>{escape(gap)}</li>" for gap in metric.gaps)
    gaps = gaps or '<li class="empty-state">No metric-specific measurement gap recorded.</li>'
    confidence = _clamp_score(metric.confidence * 100.0)

    return f"""
      <article class="metric-card">
        <header class="metric-card__header">
          <div><p class="eyebrow">Metric</p><h3>{escape(metric.title)}</h3></div>
          <div class="metric-score" aria-label="Metric score {metric.score:.1f} out of 100">
            <strong>{metric.score:.1f}</strong><span>/100</span>
          </div>
        </header>
        {_progress(metric.score, label=metric.title)}
        <p class="metric-summary">{escape(metric.summary)}</p>
        <div class="confidence-row">
          <span>Evidence confidence</span><strong>{metric.confidence:.2f}</strong>
          {_progress(confidence, label=f"{metric.title} confidence", compact=True)}
        </div>
        <section class="components" aria-label="Metric components">{components}</section>
        <div class="details-grid">
          <details><summary>Evidence <span>{len(metric.evidence)}</span></summary>
            <ul class="evidence-list">{evidence}</ul></details>
          <details><summary>Measurement gaps <span>{len(metric.gaps)}</span></summary>
            <ul class="gap-list">{gaps}</ul></details>
        </div>
      </article>
    """


def render_html(report: ProjectReport) -> str:
    """Render a portable report with inline assets and embedded report JSON."""

    snapshot = report.snapshot
    generated_at = report.generated_at.isoformat().replace("+00:00", "Z")
    project_id = escape(report.project_id)
    metrics = "".join(_metric_card(metric) for metric in report.metrics)
    gaps = "".join(f"<li>{escape(gap)}</li>" for gap in report.measurement_gaps)
    gaps = gaps or '<li class="empty-state">No project-wide measurement gap recorded.</li>'
    snapshot_values = (
        ("Events", snapshot.event_count),
        ("Sessions", snapshot.session_count),
        ("Requirements", snapshot.requirement_count),
        ("Changed files", snapshot.changed_file_count),
        ("Verifications", snapshot.verification_count),
        ("Errors", snapshot.error_count),
        ("Commits", snapshot.commit_count),
    )
    snapshot_cards = "".join(
        f'<div class="snapshot-card"><span>{escape(label)}</span><strong>{value}</strong></div>'
        for label, value in snapshot_values
    )
    report_json = _safe_embedded_json(report.to_mapping())
    file_stem = "".join(
        char if char.isalnum() or char in {"-", "_"} else "-"
        for char in report.project_id
    ).strip("-") or "loopmetry-report"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src data:; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
  <title>Loopmetry · {project_id}</title>
  <style>{REPORT_CSS}</style>
</head>
<body>
  <main class="shell">
    <section class="hero"><div class="hero__content">
      <div class="brand"><span class="brand__mark" aria-hidden="true"></span>Loopmetry</div>
      <p class="eyebrow">Project evidence report</p><h1>{project_id}</h1>
      <p class="hero__lede">Evidence-backed evaluation of how project intent moved through planning, change, verification, recovery, and delivery. Metrics remain separate; no overall developer rank is produced.</p>
      <div class="hero__meta">
        <span>Generated <code>{escape(generated_at)}</code></span>
        <span>Window <code>{escape(snapshot.started_at.isoformat().replace("+00:00", "Z"))}</code> → <code>{escape(snapshot.ended_at.isoformat().replace("+00:00", "Z"))}</code></span>
      </div>
      <div class="toolbar" aria-label="Report controls">
        <button type="button" id="expand-details">Expand evidence</button>
        <button type="button" id="collapse-details">Collapse evidence</button>
        <button type="button" id="download-json">Save report JSON</button>
      </div>
    </div></section>

    <section class="section" aria-labelledby="snapshot-title">
      <div class="section-heading"><div><p class="eyebrow">Observed volume</p><h2 id="snapshot-title">Project snapshot</h2></div>
        <p>Counts describe recorded evidence coverage. They are not productivity scores and may be incomplete when an adapter cannot observe part of the workflow.</p></div>
      <div class="snapshot-grid">{snapshot_cards}</div>
    </section>

    <section class="section" aria-labelledby="metrics-title">
      <div class="section-heading"><div><p class="eyebrow">Deterministic core</p><h2 id="metrics-title">Metric cards</h2></div>
        <p>Each card exposes components, confidence, evidence IDs, and measurement gaps. The same canonical event set produces the same deterministic result.</p></div>
      <div class="metric-grid">{metrics}</div>
    </section>

    <section class="section" aria-labelledby="signals-title">
      <div class="section-heading"><div><p class="eyebrow">Non-scored context</p><h2 id="signals-title">Workflow signals and gaps</h2></div></div>
      <div class="signal-grid">
        <article class="signal-card"><p class="eyebrow">Steering style</p>
          <div class="signal-label">{escape(report.steering.label)}</div>
          <p>{escape(report.steering.summary)}</p>
          <p><strong>Confidence:</strong> {report.steering.confidence:.2f} · <strong>Recorded interventions:</strong> {report.steering.intervention_count}</p>
        </article>
        <article class="gap-card"><p class="eyebrow">Project-wide</p><h3>Measurement gaps</h3><ul>{gaps}</ul></article>
      </div>
    </section>

    <footer class="footer">Loopmetry evaluates recorded project evidence, not developer ability, employment suitability, or individual productivity. This file is self-contained and makes no network requests.</footer>
  </main>
  <script id="loopmetry-report" type="application/json">{report_json}</script>
  <script>
    (() => {{
      const details = () => Array.from(document.querySelectorAll("details"));
      document.getElementById("expand-details").addEventListener("click", () => details().forEach(item => {{ item.open = true; }}));
      document.getElementById("collapse-details").addEventListener("click", () => details().forEach(item => {{ item.open = false; }}));
      document.getElementById("download-json").addEventListener("click", () => {{
        const payload = document.getElementById("loopmetry-report").textContent;
        const blob = new Blob([JSON.stringify(JSON.parse(payload), null, 2)], {{ type: "application/json" }});
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url; link.download = "{escape(file_stem, quote=True)}.loopmetry.json"; link.click();
        URL.revokeObjectURL(url);
      }});
    }})();
  </script>
</body>
</html>
"""

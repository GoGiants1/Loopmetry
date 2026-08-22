"""Minimal self-hosted administrator collection API and HTML dashboard."""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import html
import io
import json
import secrets
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .admin_storage import (
    REVIEW_STATUSES,
    AdminStorageError,
    AdminStore,
    StoredSubmission,
)
from .submission import SubmissionError

DEFAULT_ADMIN_BIND = "127.0.0.1"
DEFAULT_ADMIN_PORT = 8787
DEFAULT_ADMIN_TOKEN_ENV = "LOOPMETRY_ADMIN_TOKEN"
DEFAULT_MAX_SUBMISSION_BYTES = 2_000_000


class AdminServerError(ValueError):
    """Raised when the administrator server cannot be configured."""


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _basic_token(header: str | None) -> tuple[str, str] | None:
    if not header or not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator:
        return None
    return username, password


def _bearer_token(header: str | None) -> str | None:
    if not header or not header.startswith("Bearer "):
        return None
    token = header[7:].strip()
    return token or None


def _csrf_token(admin_token: str, submission_id: str) -> str:
    return hmac.new(
        admin_token.encode("utf-8"),
        f"status:{submission_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _metric_cells(submission: StoredSubmission | None) -> str:
    if submission is None:
        return '<span class="muted">—</span>'
    values = "".join(
        "<span class=\"metric-chip\">"
        f"{_escape(metric['key'])} "
        f"<strong>{float(metric['score']):.0f}</strong>"
        f"<small>c {float(metric['confidence']):.2f}</small>"
        "</span>"
        for metric in submission.metrics
    )
    return values or '<span class="muted">—</span>'


def _status_options(selected: str) -> str:
    return "".join(
        f'<option value="{_escape(status)}"'
        + (" selected" if status == selected else "")
        + f">{_escape(status.replace('_', ' '))}</option>"
        for status in REVIEW_STATUSES
    )


def _page(title: str, body: str) -> bytes:
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)}</title>
<style>
:root {{
  --bg:#f6f6f3; --surface:#fff; --text:#20201f; --muted:#6d6c68;
  --line:#deddd8; --accent:#e86f28; --accent-soft:#fff0e7;
  --ok:#2f7864; --warn:#9a6a1e; --danger:#a44a45;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);line-height:1.45}}
a{{color:#9d4818}} main{{width:min(1440px,calc(100% - 32px));margin:0 auto;padding:32px 0 64px}}
header.top{{display:flex;justify-content:space-between;align-items:end;gap:24px;margin-bottom:24px}}
.brand{{color:var(--accent);font-weight:800;letter-spacing:.08em;text-transform:uppercase;margin:0 0 4px}}
h1{{margin:0;font-size:clamp(2rem,4vw,3.5rem);letter-spacing:-.04em}} h2{{margin:0 0 14px}}
.panel,.card{{background:var(--surface);border:1px solid var(--line);border-radius:16px}}
.panel{{padding:20px;margin:16px 0}} .cards{{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:10px}}
.card{{padding:14px;min-height:90px;display:flex;flex-direction:column;justify-content:space-between}}
.card span,.muted,small{{color:var(--muted)}} .card strong{{font-size:1.8rem}}
.filters{{display:flex;flex-wrap:wrap;gap:10px;align-items:end}} label{{display:grid;gap:4px;font-size:.8rem;color:var(--muted)}}
input,select,textarea,button{{font:inherit}} input,select,textarea{{border:1px solid var(--line);border-radius:9px;padding:8px;background:#fff;color:var(--text)}}
button,.button{{border:0;border-radius:9px;padding:9px 13px;background:var(--accent);color:#fff;text-decoration:none;cursor:pointer;display:inline-block}}
.button.secondary{{background:#ecebe7;color:var(--text)}} table{{width:100%;border-collapse:collapse;font-size:.9rem}}
th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
.status{{display:inline-block;padding:4px 8px;border-radius:999px;background:#ecebe7;font:700 .75rem ui-monospace,monospace}}
.status.accepted{{background:#e7f3ed;color:var(--ok)}} .status.needs_revision{{background:#fae9e7;color:var(--danger)}}
.status.not_submitted{{background:#f3eee3;color:var(--warn)}} .metric-chip{{display:inline-flex;gap:5px;align-items:baseline;padding:4px 7px;background:#f3f2ee;border-radius:7px;margin:2px;font:600 .72rem ui-monospace,monospace}}
.metric-chip small{{font-size:.65rem}} .scroll{{overflow:auto}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
details{{border-top:1px solid var(--line);padding-top:10px;margin-top:10px}} code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}}
.notice{{background:var(--accent-soft);border:1px solid #f4cdb6;border-radius:12px;padding:12px 14px}}
footer{{color:var(--muted);font-size:.8rem;margin-top:28px}}
@media(max-width:900px){{.cards{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:1fr}}header.top{{display:block}}}}
</style>
</head>
<body><main>{body}<footer>Loopmetry administrator dashboard · {__version__} · no external assets or telemetry</footer></main></body>
</html>"""
    return document.encode("utf-8")


def _dashboard_html(
    store: AdminStore,
    *,
    assignment_id: str | None,
    status: str | None,
    query: str | None,
) -> bytes:
    overview = store.list_overview(
        assignment_id=assignment_id,
        status=status,
        query=query,
    )
    counts = store.dashboard_counts(assignment_id=assignment_id)
    assignments = store.list_assignments()
    assignment_options = '<option value="">All assignments</option>' + "".join(
        f'<option value="{_escape(item)}"'
        + (" selected" if item == assignment_id else "")
        + f">{_escape(item)}</option>"
        for item in assignments
    )
    status_options = '<option value="">All states</option>' + "".join(
        f'<option value="{_escape(item)}"'
        + (" selected" if item == status else "")
        + f">{_escape(item.replace('_', ' '))}</option>"
        for item in ("not_submitted", *REVIEW_STATUSES)
    )

    rows = []
    for item in overview:
        participant = item.participant
        latest = item.latest
        detail_link = (
            f'/submission/{urllib.parse.quote(latest.submission_id, safe="")}'
            if latest
            else ""
        )
        project = _escape(latest.project_id) if latest else '<span class="muted">—</span>'
        attempt = str(latest.attempt) if latest else "—"
        received = _escape(latest.received_at) if latest else "—"
        gaps = str(latest.measurement_gap_count) if latest else "—"
        metrics = _metric_cells(latest)
        submitter_cell = f"<strong>{_escape(participant.submitter_id)}</strong>"
        if participant.display_name:
            submitter_cell += f"<br><span class=\"muted\">{_escape(participant.display_name)}</span>"
        if detail_link:
            submitter_cell = f'<a href="{detail_link}">{submitter_cell}</a>'
        rows.append(
            "<tr>"
            f"<td>{_escape(participant.assignment_id)}</td>"
            f"<td>{submitter_cell}</td>"
            f"<td><span class=\"status {_escape(item.state)}\">{_escape(item.state)}</span></td>"
            f"<td>{project}</td><td>{attempt}</td><td>{metrics}</td><td>{gaps}</td><td>{received}</td>"
            "</tr>"
        )
    table_body = "".join(rows) or '<tr><td colspan="8" class="muted">No participants match this filter.</td></tr>'
    export_query = urllib.parse.urlencode({"assignment": assignment_id or ""})
    body = f"""
<header class="top"><div><p class="brand">Loopmetry Admin</p><h1>Submission overview</h1></div>
<div><a class="button secondary" href="/export.csv?{export_query}">Export CSV</a></div></header>
<section class="cards">
<div class="card"><span>Roster</span><strong>{counts['total']}</strong></div>
<div class="card"><span>Not submitted</span><strong>{counts['not_submitted']}</strong></div>
<div class="card"><span>Received / reviewing</span><strong>{counts['received'] + counts['reviewing']}</strong></div>
<div class="card"><span>Needs revision</span><strong>{counts['needs_revision']}</strong></div>
<div class="card"><span>Accepted</span><strong>{counts['accepted']}</strong></div>
</section>
<section class="panel"><form class="filters" method="get" action="/">
<label>Assignment<select name="assignment">{assignment_options}</select></label>
<label>State<select name="status">{status_options}</select></label>
<label>Search<input name="q" value="{_escape(query or '')}" placeholder="ID or name"></label>
<button type="submit">Filter</button><a class="button secondary" href="/">Reset</a>
</form></section>
<section class="panel scroll"><table><thead><tr>
<th>Assignment</th><th>Submitter</th><th>State</th><th>Project</th><th>Attempt</th><th>Metrics</th><th>Gaps</th><th>Received</th>
</tr></thead><tbody>{table_body}</tbody></table></section>
<p class="notice">Rows are ordered by roster identity, not by score. Review status is assigned manually; Loopmetry does not rank participants.</p>
"""
    return _page("Loopmetry administrator dashboard", body)


def _metric_detail(metric: Mapping[str, Any]) -> str:
    components = "".join(
        f"<tr><td>{_escape(key)}</td><td>{float(value):.1f}</td></tr>"
        for key, value in dict(metric.get("components", {})).items()
    )
    evidence = "".join(
        "<li>"
        f"<code>{_escape(item.get('event_id', ''))}</code> · {_escape(item.get('event_type', ''))}"
        f"<br>{_escape(item.get('summary', ''))}</li>"
        for item in metric.get("evidence", [])
        if isinstance(item, Mapping)
    )
    gaps = "".join(f"<li>{_escape(item)}</li>" for item in metric.get("gaps", []))
    return f"""
<article class="panel"><h2>{_escape(metric.get('title', metric.get('key', 'Metric')))}</h2>
<p><strong>{float(metric.get('score', 0)):.1f}/100</strong> · confidence {float(metric.get('confidence', 0)):.2f}</p>
<p>{_escape(metric.get('summary', ''))}</p>
<table><thead><tr><th>Component</th><th>Score</th></tr></thead><tbody>{components}</tbody></table>
<details><summary>Evidence ({len(metric.get('evidence', []))})</summary><ul>{evidence or '<li class="muted">None</li>'}</ul></details>
<details><summary>Measurement gaps ({len(metric.get('gaps', []))})</summary><ul>{gaps or '<li class="muted">None</li>'}</ul></details>
</article>"""


def _submission_html(store: AdminStore, submission: StoredSubmission, admin_token: str) -> bytes:
    report = submission.envelope["report"]
    metrics = "".join(_metric_detail(item) for item in report.get("metrics", []))
    history = store.status_history(submission.submission_id)
    history_html = "".join(
        f"<li><strong>{_escape(item['status'])}</strong> · {_escape(item['changed_at'])}"
        + (f"<br>{_escape(item['note'])}" if item.get("note") else "")
        + "</li>"
        for item in history
    )
    participant_history = store.list_submission_history(
        assignment_id=submission.assignment_id,
        submitter_id=submission.submitter_id,
    )
    attempts = "".join(
        f'<li><a href="/submission/{urllib.parse.quote(item.submission_id, safe="")}">Attempt {item.attempt}</a> · {_escape(item.status)} · {_escape(item.received_at)}</li>'
        for item in participant_history
    )
    csrf = _csrf_token(admin_token, submission.submission_id)
    body = f"""
<header class="top"><div><p class="brand">Loopmetry Admin</p><h1>{_escape(submission.submitter_id)}</h1>
<p class="muted">{_escape(submission.display_name)} · {_escape(submission.assignment_id)}</p></div>
<a class="button secondary" href="/">Back to overview</a></header>
<section class="grid"><div class="panel"><h2>Submission</h2>
<p><strong>Project</strong> {_escape(submission.project_id)}</p><p><strong>Attempt</strong> {submission.attempt}</p>
<p><strong>Submission ID</strong><br><code>{_escape(submission.submission_id)}</code></p>
<p><strong>Received</strong> {_escape(submission.received_at)}</p>
<p><strong>Events / sessions</strong> {submission.event_count} / {submission.session_count}</p>
<p><strong>Measurement gaps</strong> {submission.measurement_gap_count}</p></div>
<div class="panel"><h2>Review state</h2>
<form method="post" action="/submission/{urllib.parse.quote(submission.submission_id, safe='')}/status">
<input type="hidden" name="csrf" value="{csrf}"><label>Status<select name="status">{_status_options(submission.status)}</select></label><br>
<label>Reviewer note<textarea name="note" rows="5" maxlength="4000">{_escape(submission.reviewer_note)}</textarea></label><br>
<button type="submit">Save review state</button></form></div></section>
<section class="grid"><div class="panel"><h2>Attempt history</h2><ul>{attempts}</ul></div>
<div class="panel"><h2>Status history</h2><ul>{history_html}</ul></div></section>
{metrics}
<section class="panel"><details><summary>Validated submission envelope</summary><pre><code>{_escape(json.dumps(submission.envelope, ensure_ascii=False, indent=2))}</code></pre></details></section>
"""
    return _page(f"Loopmetry submission {submission.submitter_id}", body)


class LoopmetryAdminHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        store: AdminStore,
        admin_token: str,
        max_submission_bytes: int,
    ):
        super().__init__(server_address, LoopmetryAdminHandler)
        self.store = store
        self.admin_token = admin_token
        self.max_submission_bytes = max_submission_bytes


class LoopmetryAdminHandler(BaseHTTPRequestHandler):
    server_version = "LoopmetryAdmin/1.0"

    @property
    def admin_server(self) -> LoopmetryAdminHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: object) -> None:
        # Avoid logging authorization headers or request bodies; the default line is safe.
        super().log_message(format, *args)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(
        self,
        status: HTTPStatus | int,
        body: bytes,
        *,
        content_type: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(int(status))
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus | int, value: Mapping[str, object]) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, content_type="application/json; charset=utf-8")

    def _error(self, status: HTTPStatus | int, message: str) -> None:
        self._json(status, {"error": message})

    def _is_admin(self) -> bool:
        credentials = _basic_token(self.headers.get("Authorization"))
        if credentials is None:
            return False
        username, password = credentials
        return username == "admin" and secrets.compare_digest(
            password, self.admin_server.admin_token
        )

    def _require_admin(self) -> bool:
        if self._is_admin():
            return True
        self._send_bytes(
            HTTPStatus.UNAUTHORIZED,
            b"Administrator authentication required.\n",
            content_type="text/plain; charset=utf-8",
            extra_headers={"WWW-Authenticate": 'Basic realm="Loopmetry Admin"'},
        )
        return False

    def _read_json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise SubmissionError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise SubmissionError("invalid Content-Length") from exc
        if length < 1 or length > self.admin_server.max_submission_bytes:
            raise SubmissionError(
                f"submission body must be 1..{self.admin_server.max_submission_bytes} bytes"
            )
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            raise SubmissionError("Content-Type must be application/json")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SubmissionError("request body is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise SubmissionError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True, "version": __version__})
            return
        if not self._require_admin():
            return
        query = urllib.parse.parse_qs(parsed.query)
        assignment = query.get("assignment", [""])[0] or None
        status = query.get("status", [""])[0] or None
        search = query.get("q", [""])[0] or None

        if parsed.path == "/":
            self._send_bytes(
                HTTPStatus.OK,
                _dashboard_html(
                    self.admin_server.store,
                    assignment_id=assignment,
                    status=status,
                    query=search,
                ),
                content_type="text/html; charset=utf-8",
            )
            return
        if parsed.path == "/api/v1/participants":
            overview = self.admin_server.store.list_overview(
                assignment_id=assignment,
                status=status,
                query=search,
            )
            self._json(
                HTTPStatus.OK,
                {
                    "participants": [
                        {
                            "assignment_id": item.participant.assignment_id,
                            "submitter_id": item.participant.submitter_id,
                            "display_name": item.participant.display_name,
                            "state": item.state,
                            "latest_submission": (
                                item.latest.summary_mapping() if item.latest else None
                            ),
                        }
                        for item in overview
                    ]
                },
            )
            return
        if parsed.path == "/api/v1/submissions":
            overview = self.admin_server.store.list_overview(
                assignment_id=assignment,
                status=status,
                query=search,
            )
            self._json(
                HTTPStatus.OK,
                {
                    "submissions": [
                        item.latest.summary_mapping()
                        for item in overview
                        if item.latest is not None
                    ]
                },
            )
            return
        if parsed.path == "/export.csv":
            rows = self.admin_server.store.export_rows(assignment_id=assignment)
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
            self._send_bytes(
                HTTPStatus.OK,
                buffer.getvalue().encode("utf-8-sig"),
                content_type="text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": 'attachment; filename="loopmetry-submissions.csv"'},
            )
            return
        if parsed.path.startswith("/submission/"):
            submission_id = urllib.parse.unquote(parsed.path[len("/submission/") :])
            submission = self.admin_server.store.get_submission(submission_id)
            if submission is None:
                self._error(HTTPStatus.NOT_FOUND, "submission not found")
                return
            self._send_bytes(
                HTTPStatus.OK,
                _submission_html(
                    self.admin_server.store,
                    submission,
                    self.admin_server.admin_token,
                ),
                content_type="text/html; charset=utf-8",
            )
            return
        if parsed.path.startswith("/api/v1/submissions/"):
            submission_id = urllib.parse.unquote(
                parsed.path[len("/api/v1/submissions/") :]
            )
            submission = self.admin_server.store.get_submission(submission_id)
            if submission is None:
                self._error(HTTPStatus.NOT_FOUND, "submission not found")
                return
            self._json(
                HTTPStatus.OK,
                {
                    **submission.summary_mapping(),
                    "envelope": submission.envelope,
                    "status_history": self.admin_server.store.status_history(submission_id),
                },
            )
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/v1/submissions":
            token = _bearer_token(self.headers.get("Authorization"))
            participant = self.admin_server.store.authenticate(token or "")
            if participant is None:
                self._error(HTTPStatus.UNAUTHORIZED, "invalid or inactive enrollment token")
                return
            try:
                envelope = self._read_json_body()
                inserted = self.admin_server.store.add_submission(
                    envelope,
                    participant=participant,
                )
            except (SubmissionError, AdminStorageError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            stored = inserted.stored
            self._json(
                HTTPStatus.OK if inserted.duplicate else HTTPStatus.CREATED,
                {
                    "submission_id": stored.submission_id,
                    "assignment_id": stored.assignment_id,
                    "submitter_id": stored.submitter_id,
                    "attempt": stored.attempt,
                    "status": stored.status,
                    "duplicate": inserted.duplicate,
                    "received_at": stored.received_at,
                },
            )
            return

        if not self._require_admin():
            return
        if parsed.path.startswith("/submission/") and parsed.path.endswith("/status"):
            submission_id = urllib.parse.unquote(
                parsed.path[len("/submission/") : -len("/status")].rstrip("/")
            )
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
                return
            if length < 1 or length > 16_384:
                self._error(HTTPStatus.BAD_REQUEST, "invalid form body size")
                return
            try:
                body = self.rfile.read(length).decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                self._error(HTTPStatus.BAD_REQUEST, "form body must be UTF-8")
                return
            form = urllib.parse.parse_qs(body, keep_blank_values=True)
            csrf = form.get("csrf", [""])[0]
            expected = _csrf_token(self.admin_server.admin_token, submission_id)
            if not secrets.compare_digest(csrf, expected):
                self._error(HTTPStatus.FORBIDDEN, "invalid CSRF token")
                return
            status = form.get("status", [""])[0]
            note = form.get("note", [""])[0]
            try:
                self.admin_server.store.update_status(submission_id, status, note)
            except AdminStorageError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            self._security_headers()
            self.send_header(
                "Location",
                "/submission/" + urllib.parse.quote(submission_id, safe=""),
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")


def create_admin_server(
    *,
    database: str | Path,
    admin_token: str,
    bind: str = DEFAULT_ADMIN_BIND,
    port: int = DEFAULT_ADMIN_PORT,
    max_submission_bytes: int = DEFAULT_MAX_SUBMISSION_BYTES,
) -> LoopmetryAdminHTTPServer:
    normalized_token = admin_token.strip()
    if len(normalized_token) < 16:
        raise AdminServerError("administrator token must be at least 16 characters")
    if not 0 <= port <= 65535:
        raise AdminServerError("port must be in the range 0..65535")
    if max_submission_bytes < 1:
        raise AdminServerError("max_submission_bytes must be positive")
    return LoopmetryAdminHTTPServer(
        (bind, port),
        store=AdminStore(database),
        admin_token=normalized_token,
        max_submission_bytes=max_submission_bytes,
    )

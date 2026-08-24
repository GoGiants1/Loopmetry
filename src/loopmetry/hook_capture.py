"""Privacy-preserving capture of Claude Code and Codex lifecycle hook payloads.

The adapter emits canonical Loopmetry events directly and deliberately omits raw prompts, source
code, complete tool output, absolute path prefixes, and full shell commands.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping

from .schema import Actor, Event, EventType

HOOK_ADAPTER_VERSION = "1.0.0"

_SUPPORTED_SOURCES = {"claude-code", "codex"}
_PATH_KEYS = ("file_path", "filePath", "path", "notebook_path", "notebookPath")
_PATCH_FILE_RE = re.compile(r"^\*\*\*\s+(Add|Update|Delete)\s+File:\s*(.+?)\s*$", re.MULTILINE)
_GIT_PATCH_PATH_RE = re.compile(r"^(?:\+\+\+|---)\s+[ab]/(.+?)\s*$", re.MULTILINE)
_EXIT_CODE_RE = re.compile(r"(?:exit(?:ed)?(?:\s+with)?\s+code|return\s+code)\D*(-?\d+)", re.I)
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_STATUS_CONTAINER_KEYS = frozenset(
    {"output", "content", "stdout", "stderr", "message", "text", "result", "details", "tool_result"}
)
_MAX_STATUS_DEPTH = 4
_MAX_APPEND_BYTES = 1_000_000


class HookCaptureError(ValueError):
    """Raised when an incoming hook payload cannot be captured safely."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _hash_text(encoded)


def _safe_identifier(value: str, *, fallback: str) -> str:
    normalized = _SAFE_ID_RE.sub("-", value.strip()).strip("-._")
    return normalized[:80] or fallback


def derive_project_id(cwd: str) -> str:
    """Derive a stable, pseudonymous project ID from the working directory."""

    path = Path(cwd).expanduser()
    label = _safe_identifier(path.name or "project", fallback="project").lower()
    digest = _hash_text(str(path.resolve()))[:10]
    return f"{label}-{digest}"


def _safe_path(value: object, cwd: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("\\", "/")

    # Handle Windows drive and UNC paths lexically even when Loopmetry itself runs on Unix.
    windows_candidate = PureWindowsPath(value.strip())
    windows_root = PureWindowsPath(cwd)
    if windows_candidate.is_absolute() or windows_candidate.drive:
        try:
            relative = windows_candidate.relative_to(windows_root)
        except ValueError:
            basename = windows_candidate.name or "unknown"
            return f"<external-path-redacted>/{basename}"
        return PurePosixPath(*relative.parts).as_posix()

    root = Path(cwd).expanduser()
    candidate = Path(value).expanduser()
    try:
        if candidate.is_absolute():
            relative = candidate.resolve().relative_to(root.resolve())
            return relative.as_posix()
    except (OSError, ValueError):
        basename = PurePosixPath(raw).name or "unknown"
        return f"<external-path-redacted>/{basename}"

    parts = [part for part in PurePosixPath(raw).parts if part not in {".", "/"}]
    if ".." in parts:
        return f"<traversal-redacted>/{parts[-1] if parts else 'unknown'}"
    return "/".join(parts) or None


def _extract_paths(tool_input: object, *, cwd: str) -> list[tuple[str, str | None]]:
    """Return (path, action) pairs without retaining patch content."""

    results: list[tuple[str, str | None]] = []
    if isinstance(tool_input, Mapping):
        for key in _PATH_KEYS:
            safe = _safe_path(tool_input.get(key), cwd)
            if safe:
                results.append((safe, None))
        for key in ("paths", "files"):
            values = tool_input.get(key)
            if isinstance(values, list):
                for value in values:
                    safe = _safe_path(value, cwd)
                    if safe:
                        results.append((safe, None))

        for content_key in ("command", "patch"):
            patch_content = tool_input.get(content_key)
            if not isinstance(patch_content, str):
                continue
            for action, path in _PATCH_FILE_RE.findall(patch_content):
                safe = _safe_path(path, cwd)
                if safe:
                    normalized_action = {
                        "Add": "add",
                        "Update": "modify",
                        "Delete": "delete",
                    }[action]
                    results.append((safe, normalized_action))
            for path in _GIT_PATCH_PATH_RE.findall(patch_content):
                if path == "/dev/null":
                    continue
                safe = _safe_path(path, cwd)
                if safe:
                    results.append((safe, None))

    deduplicated: dict[str, str | None] = {}
    for path, action in results:
        if path not in deduplicated or action is not None:
            deduplicated[path] = action
    return list(deduplicated.items())


def _iter_status_nodes(value: object, *, depth: int = 0) -> Iterable[object]:
    """Yield bounded, allowlisted response fragments used only for status inference."""

    if depth > _MAX_STATUS_DEPTH:
        return
    if isinstance(value, str):
        yield value[:2_000]
        return
    if isinstance(value, Mapping):
        yield value
        for key in _STATUS_CONTAINER_KEYS:
            if key in value:
                yield from _iter_status_nodes(value[key], depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value[:100]:
            yield from _iter_status_nodes(item, depth=depth + 1)


def _tool_status(payload: Mapping[str, Any]) -> str:
    hook_event = str(payload.get("hook_event_name", ""))
    if hook_event in {"PostToolUseFailure", "StopFailure"}:
        return "failed"

    response = payload.get("tool_response")
    for node in _iter_status_nodes(response):
        if isinstance(node, Mapping):
            success = node.get("success")
            if isinstance(success, bool):
                return "success" if success else "failed"
            for key in ("exit_code", "exitCode", "return_code", "returnCode"):
                value = node.get(key)
                if isinstance(value, int):
                    return "success" if value == 0 else "failed"
            status = node.get("status")
            if isinstance(status, str):
                lowered = status.lower()
                if lowered in {"success", "succeeded", "completed", "passed", "ok"}:
                    return "success"
                if lowered in {"failed", "failure", "error", "errored"}:
                    return "failed"
        elif isinstance(node, str):
            match = _EXIT_CODE_RE.search(node)
            if match:
                return "success" if int(match.group(1)) == 0 else "failed"

    # Claude Code exposes failures through PostToolUseFailure, so ordinary PostToolUse means the
    # tool completed successfully. Codex response shapes can vary; preserve unknown when no explicit
    # status is available instead of inventing success.
    if payload.get("source") == "claude-code" and hook_event == "PostToolUse":
        return "success"
    return "unknown"


def _raw_command(tool_input: object) -> str | None:
    if not isinstance(tool_input, Mapping):
        return None
    for key in ("command", "cmd"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _command_signature(command: str) -> tuple[str, str | None]:
    """Return a content-minimized command label and optional verification kind."""

    lowered = " ".join(command.lower().split())
    rules: tuple[tuple[str, str, str | None], ...] = (
        (r"\b(pytest|python\s+-m\s+pytest)\b", "pytest", "test"),
        (r"\bpython\s+-m\s+unittest\b", "python -m unittest", "test"),
        (r"\b(go\s+test)\b", "go test", "test"),
        (r"\b(cargo\s+test)\b", "cargo test", "test"),
        (r"\b(dotnet\s+test)\b", "dotnet test", "test"),
        (r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?test\b", "javascript test", "test"),
        (r"\b(mvn|mvnw)\b.*\btest\b", "maven test", "test"),
        (r"\b(gradle|gradlew)\b.*\btest\b", "gradle test", "test"),
        (r"\b(tox|nox)\b", "python test environment", "test"),
        (r"\b(ruff|flake8|pylint|eslint|biome)\b", "lint", "lint"),
        (r"\b(mypy|pyright|pyre|tsc)\b", "type check", "typecheck"),
        (r"\b(bandit|semgrep|trivy|grype|gitleaks)\b", "security scan", "security"),
        (r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?build\b", "javascript build", "build"),
        (r"\b(cargo\s+build|go\s+build|dotnet\s+build|mvn\b.*package|gradle\b.*build)\b", "build", "build"),
        (r"\buv\s+build\b", "uv build", "build"),
        (r"\bgit\s+commit\b", "git commit", None),
    )
    for pattern, label, kind in rules:
        if re.search(pattern, lowered):
            return label, kind

    try:
        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        tokens = command.split()
    if not tokens:
        return "shell command", None
    executable = PurePosixPath(tokens[0].replace("\\", "/")).name
    return _safe_identifier(executable.lower(), fallback="shell-command"), None


def _event_id(
    payload: Mapping[str, Any], event_type: EventType, suffix: str, *, source: str
) -> str:
    stable = {
        "source": source,
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "tool_use_id": payload.get("tool_use_id"),
        "hook_event_name": payload.get("hook_event_name"),
        "event_type": event_type.value,
        "suffix": suffix,
        # A hash of the input distinguishes repeated identical-looking hooks without storing content.
        "payload_hash": _canonical_hash(payload),
    }
    return f"hook-{_canonical_hash(stable)[:24]}"


def _base_event(
    payload: Mapping[str, Any],
    *,
    source: str,
    project_id: str,
    captured_at: datetime,
    event_type: EventType,
    actor: Actor,
    data: Mapping[str, Any],
    suffix: str,
) -> Event:
    session_id = str(payload.get("session_id") or "unknown-session")
    return Event.from_mapping(
        {
            "schema_version": "0.2",
            "event_id": _event_id(payload, event_type, suffix, source=source),
            "project_id": project_id,
            "session_id": session_id,
            "timestamp": captured_at.isoformat().replace("+00:00", "Z"),
            "type": event_type.value,
            "actor": actor.value,
            "source": source,
            "data": dict(data),
            "provenance": [
                {
                    "source": source,
                    "capture_mode": "hook",
                    "adapter_version": HOOK_ADAPTER_VERSION,
                }
            ],
        }
    )


def normalize_hook_payload(
    payload: Mapping[str, Any],
    *,
    source: str,
    project_id: str | None = None,
    captured_at: datetime | None = None,
) -> list[Event]:
    """Convert one official hook payload into zero or more canonical events."""

    if source not in _SUPPORTED_SOURCES:
        raise HookCaptureError(f"source must be one of {sorted(_SUPPORTED_SOURCES)}")
    if not isinstance(payload, Mapping):
        raise HookCaptureError("hook payload must be a JSON object")
    cwd = str(payload.get("cwd") or ".")
    resolved_project_id = project_id or derive_project_id(cwd)
    timestamp = captured_at or _utc_now()
    hook_event = str(payload.get("hook_event_name") or "")
    tool_name = str(payload.get("tool_name") or "")
    tool_lower = tool_name.lower()
    events: list[Event] = []

    if hook_event == "UserPromptSubmit":
        prompt = payload.get("prompt")
        prompt_text = prompt if isinstance(prompt, str) else ""
        events.append(
            _base_event(
                payload,
                source=source,
                project_id=resolved_project_id,
                captured_at=timestamp,
                event_type=EventType.HUMAN_INTERVENTION,
                actor=Actor.HUMAN,
                data={
                    "action": "prompt",
                    "summary": "User submitted a prompt; content omitted.",
                    "prompt_sha256": _hash_text(prompt_text),
                    "prompt_length": len(prompt_text),
                },
                suffix="prompt",
            )
        )
        return events

    if hook_event in {
        "SessionStart",
        "SessionEnd",
        "Stop",
        "StopFailure",
        "SubagentStart",
        "SubagentStop",
        "TaskCompleted",
    }:
        summary = {
            "SessionStart": "Agent session started.",
            "SessionEnd": "Agent session ended.",
            "Stop": "Agent turn stopped.",
            "StopFailure": "Agent turn stopped after a failure.",
            "SubagentStart": "Subagent started.",
            "SubagentStop": "Subagent stopped.",
            "TaskCompleted": "Agent task completed.",
        }[hook_event]
        data: dict[str, Any] = {"summary": summary, "hook_event": hook_event}
        for key in ("reason", "permission_mode", "model"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                data[key] = value[:120]
        events.append(
            _base_event(
                payload,
                source=source,
                project_id=resolved_project_id,
                captured_at=timestamp,
                event_type=EventType.NOTE,
                actor=Actor.SYSTEM,
                data=data,
                suffix=hook_event.lower(),
            )
        )
        return events

    if hook_event == "TaskCreated" or tool_lower in {
        "update_plan",
        "enterplanmode",
        "exitplanmode",
        "todowrite",
        "taskcreate",
    }:
        events.append(
            _base_event(
                payload,
                source=source,
                project_id=resolved_project_id,
                captured_at=timestamp,
                event_type=EventType.PLAN,
                actor=Actor.AGENT,
                data={"summary": "Agent created or updated a plan; plan text omitted."},
                suffix="plan",
            )
        )
        if hook_event != "PostToolUse":
            return events

    if hook_event not in {"PostToolUse", "PostToolUseFailure"}:
        return events

    tool_input = payload.get("tool_input")
    paths = _extract_paths(tool_input, cwd=cwd)

    read_like = tool_lower in {"read", "readfile", "read_file"} or tool_lower.endswith("__read_file")
    change_like = tool_lower in {
        "write",
        "edit",
        "multiedit",
        "apply_patch",
        "notebookedit",
        "write_file",
    } or tool_lower.endswith("__write_file")

    if read_like:
        for index, (path, _) in enumerate(paths):
            events.append(
                _base_event(
                    payload,
                    source=source,
                    project_id=resolved_project_id,
                    captured_at=timestamp,
                    event_type=EventType.FILE_READ,
                    actor=Actor.AGENT,
                    data={"path": path},
                    suffix=f"read-{index}-{path}",
                )
            )

    if change_like:
        if not paths:
            # A content-free note preserves adapter coverage without inventing a file path.
            events.append(
                _base_event(
                    payload,
                    source=source,
                    project_id=resolved_project_id,
                    captured_at=timestamp,
                    event_type=EventType.NOTE,
                    actor=Actor.AGENT,
                    data={
                        "summary": "A file-edit tool completed, but no safe path was extractable.",
                        "tool_name": tool_name,
                    },
                    suffix="change-without-path",
                )
            )
        for index, (path, action) in enumerate(paths):
            events.append(
                _base_event(
                    payload,
                    source=source,
                    project_id=resolved_project_id,
                    captured_at=timestamp,
                    event_type=EventType.FILE_CHANGE,
                    actor=Actor.AGENT,
                    data={"path": path, "action": action or "modify"},
                    suffix=f"change-{index}-{path}",
                )
            )

    command = _raw_command(tool_input)
    shell_like = tool_lower in {"bash", "shell", "exec_command", "run_command"}
    if shell_like and command:
        status = _tool_status({**payload, "source": source})
        label, verification_kind = _command_signature(command)
        command_data = {
            "command": label,
            "status": status,
            "command_sha256": _hash_text(command),
            "tool_name": tool_name,
        }
        events.append(
            _base_event(
                payload,
                source=source,
                project_id=resolved_project_id,
                captured_at=timestamp,
                event_type=EventType.COMMAND,
                actor=Actor.TOOL,
                data=command_data,
                suffix="command",
            )
        )

        if verification_kind:
            verification_status = {
                "success": "passed",
                "failed": "failed",
                "error": "error",
                "unknown": "skipped",
            }[status]
            events.append(
                _base_event(
                    payload,
                    source=source,
                    project_id=resolved_project_id,
                    captured_at=timestamp,
                    event_type=EventType.VERIFICATION,
                    actor=Actor.TOOL,
                    data={
                        "kind": verification_kind,
                        "status": verification_status,
                        "command": label,
                    },
                    suffix=f"verification-{verification_kind}",
                )
            )

        if status in {"failed", "error"}:
            events.append(
                _base_event(
                    payload,
                    source=source,
                    project_id=resolved_project_id,
                    captured_at=timestamp,
                    event_type=EventType.ERROR,
                    actor=Actor.TOOL,
                    data={
                        "code": "TOOL_EXIT_NONZERO",
                        "message": f"{label} failed; output omitted.",
                    },
                    suffix="command-error",
                )
            )

        if label == "git commit" and status == "success":
            try:
                completed = subprocess.run(
                    ["git", "-C", cwd, "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                completed = None
            if completed is not None and completed.returncode == 0:
                sha = completed.stdout.strip()
                if sha:
                    events.append(
                        _base_event(
                            payload,
                            source=source,
                            project_id=resolved_project_id,
                            captured_at=timestamp,
                            event_type=EventType.COMMIT,
                            actor=Actor.TOOL,
                            data={"sha": sha, "summary": "Commit created; message omitted."},
                            suffix=f"commit-{sha}",
                        )
                    )

    return events


def default_capture_path(payload: Mapping[str, Any], *, source: str) -> Path:
    cwd = Path(str(payload.get("cwd") or ".")).expanduser()
    return cwd / ".loopmetry" / "hooks" / f"{source}.jsonl"


def append_events(path: str | Path, events: Iterable[Event]) -> int:
    """Append normalized events atomically enough for short concurrent hook writes."""

    materialized = list(events)
    if not materialized:
        return 0
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.parent.chmod(0o700)
    except OSError:
        pass
    payload = "".join(
        json.dumps(event.to_mapping(), ensure_ascii=False, separators=(",", ":")) + "\n"
        for event in materialized
    ).encode("utf-8")
    if len(payload) > _MAX_APPEND_BYTES:
        raise HookCaptureError(
            f"one hook capture append exceeds {_MAX_APPEND_BYTES} bytes; refusing a multi-write record"
        )
    descriptor = os.open(output, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise OSError(f"short append: wrote {written} of {len(payload)} bytes")
    finally:
        os.close(descriptor)
    return len(materialized)

"""Shared content-minimization helpers used by every source adapter."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from pathlib import Path, PurePosixPath, PureWindowsPath

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hash_text(encoded)


def safe_identifier(value: str, *, fallback: str) -> str:
    normalized = _SAFE_ID_RE.sub("-", value.strip()).strip("-._")
    return normalized[:80] or fallback


def derive_project_id(cwd: str) -> str:
    """Derive a stable, pseudonymous project ID from the working directory."""

    path = Path(cwd).expanduser()
    label = safe_identifier(path.name or "project", fallback="project").lower()
    digest = hash_text(str(path.resolve()))[:10]
    return f"{label}-{digest}"


def safe_relative_path(value: object, cwd: str) -> str | None:
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


def command_signature(command: str) -> tuple[str, str | None]:
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
    return safe_identifier(executable.lower(), fallback="shell-command"), None

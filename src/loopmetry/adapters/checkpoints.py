"""Atomic local persistence for incremental-import checkpoints."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .base import AdapterError, Checkpoint

_SAFE_SOURCE_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def checkpoint_path(project_root: Path, source: str) -> Path:
    safe = _SAFE_SOURCE_RE.sub("-", source).strip("-._") or "source"
    return Path(project_root).expanduser() / ".loopmetry" / "checkpoints" / f"{safe}.json"


def load_checkpoint(project_root: Path, source: str) -> Checkpoint | None:
    path = checkpoint_path(project_root, source)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"corrupt checkpoint file: {path}") from exc
    checkpoint = Checkpoint.from_mapping(raw)
    if checkpoint.source != source:
        raise AdapterError(
            f"checkpoint at {path} has source {checkpoint.source!r}, expected {source!r}"
        )
    return checkpoint


def save_checkpoint(project_root: Path, checkpoint: Checkpoint) -> Path:
    path = checkpoint_path(project_root, checkpoint.source)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    payload = json.dumps(checkpoint.to_mapping(), ensure_ascii=False, indent=2) + "\n"
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    except OSError:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return path

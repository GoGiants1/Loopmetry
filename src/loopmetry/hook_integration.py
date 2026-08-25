"""Deterministic generation and merging of local hook configuration.

Pure logic only — no filesystem access. `cli.py` owns reading the existing
file, diffing, backup, and the force-to-modify-an-existing-file policy.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

INTEGRATION_HOOK_EVENTS = (
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "TaskCompleted",
    "SessionEnd",
)

_COMMAND_PREFIX = "loopmetry capture-hook --source claude-code"


def build_hook_command(project_id: str | None) -> str:
    if project_id:
        return f"{_COMMAND_PREFIX} --project-id {project_id}"
    return _COMMAND_PREFIX


def _is_managed_block(block: Any, command: str | None = None) -> bool:
    if not isinstance(block, Mapping):
        return False
    entries = block.get("hooks")
    if not isinstance(entries, list) or len(entries) != 1:
        return False
    entry = entries[0]
    if not isinstance(entry, Mapping):
        return False
    entry_command = entry.get("command")
    if not isinstance(entry_command, str) or not entry_command.startswith(_COMMAND_PREFIX):
        return False
    if command is not None and entry_command != command:
        return False
    return entry.get("type") == "command"


def merge_settings(
    existing: Mapping[str, Any], project_id: str | None
) -> tuple[dict[str, Any], bool]:
    merged: dict[str, Any] = json.loads(json.dumps(existing))
    command = build_hook_command(project_id)
    changed = False
    hooks = merged.setdefault("hooks", {})
    for event in INTEGRATION_HOOK_EVENTS:
        blocks = hooks.setdefault(event, [])
        if not isinstance(blocks, list):
            continue
        if any(_is_managed_block(block, command) for block in blocks):
            continue
        blocks.append({"hooks": [{"type": "command", "command": command}]})
        changed = True
    return merged, changed


def remove_settings(existing: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    merged: dict[str, Any] = json.loads(json.dumps(existing))
    changed = False
    hooks = merged.get("hooks")
    if not isinstance(hooks, dict):
        return merged, changed
    for event in list(hooks.keys()):
        blocks = hooks.get(event)
        if not isinstance(blocks, list):
            continue
        kept = [block for block in blocks if not _is_managed_block(block)]
        if len(kept) != len(blocks):
            changed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        merged.pop("hooks", None)
    return merged, changed


def format_settings(data: Mapping[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

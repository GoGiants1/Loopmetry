"""Deterministic generation and merging of local hook configuration.

Pure logic only — no filesystem access. `cli.py` owns reading the existing
file, diffing, backup, and the force-to-modify-an-existing-file policy.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Mapping

INTEGRATION_HOOK_EVENTS = (
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "TaskCompleted",
    "SessionEnd",
)

_COMMAND = "loopmetry"
_BASE_ARGS = ["capture-hook", "--source", "claude-code"]


def build_hook_args(project_id: str | None) -> list[str]:
    # Exec form (command + args, no shell) rather than a single shell-parsed
    # command string: project_id is user-supplied and must never be interpreted
    # for spaces, quoting, or shell metacharacters by the hook's shell.
    if project_id:
        return [*_BASE_ARGS, "--project-id", project_id]
    return list(_BASE_ARGS)


def _owned_args(entry: Any) -> list[str] | None:
    """Return this handler's args if it is exactly an installer-generated
    command handler for any project_id, else None. Ownership requires an exact
    structural match (only "type"/"command"/"args" keys) so a handler with an
    extra filtering field such as "if" is never mistaken for ours."""
    if not isinstance(entry, Mapping) or set(entry.keys()) != {"type", "command", "args"}:
        return None
    if entry.get("type") != "command" or entry.get("command") != _COMMAND:
        return None
    args = entry.get("args")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return None
    prefix, remainder = args[: len(_BASE_ARGS)], args[len(_BASE_ARGS) :]
    if prefix != _BASE_ARGS:
        return None
    if remainder and (len(remainder) != 2 or remainder[0] != "--project-id"):
        return None
    return args


def _owned_block(block: Any) -> list[str] | None:
    """Return the owned handler's args if `block` is exactly a single-handler,
    unrestricted (no "matcher") installer block, else None. A block scoped by
    "matcher" (e.g. "Bash") only fires for a subset of the event's occurrences
    and must never be treated as satisfying full integration."""
    if not isinstance(block, Mapping) or set(block.keys()) != {"hooks"}:
        return None
    entries = block.get("hooks")
    if not isinstance(entries, list) or len(entries) != 1:
        return None
    return _owned_args(entries[0])


def _hooks_dict(merged: dict[str, Any]) -> dict[str, Any]:
    hooks = merged.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' must be a JSON object")
    return hooks


def merge_settings(
    existing: Mapping[str, Any], project_id: str | None
) -> tuple[dict[str, Any], bool]:
    merged: dict[str, Any] = copy.deepcopy(dict(existing))
    desired_args = build_hook_args(project_id)
    changed = False
    hooks = _hooks_dict(merged)
    for event in INTEGRATION_HOOK_EVENTS:
        blocks = hooks.setdefault(event, [])
        if not isinstance(blocks, list):
            raise ValueError(f"'hooks.{event}' must be a JSON array")
        owned = [(i, _owned_block(block)) for i, block in enumerate(blocks)]
        owned = [(i, args) for i, args in owned if args is not None]
        if len(owned) == 1 and owned[0][1] == desired_args:
            continue
        owned_indexes = {i for i, _ in owned}
        blocks[:] = [block for i, block in enumerate(blocks) if i not in owned_indexes]
        blocks.append(
            {"hooks": [{"type": "command", "command": _COMMAND, "args": desired_args}]}
        )
        changed = True
    return merged, changed


def remove_settings(existing: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    merged: dict[str, Any] = copy.deepcopy(dict(existing))
    changed = False
    hooks = merged.get("hooks")
    if hooks is None:
        return merged, changed
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' must be a JSON object")
    for event in INTEGRATION_HOOK_EVENTS:
        if event not in hooks:
            continue
        blocks = hooks[event]
        if not isinstance(blocks, list):
            raise ValueError(f"'hooks.{event}' must be a JSON array")
        kept = [block for block in blocks if _owned_block(block) is None]
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

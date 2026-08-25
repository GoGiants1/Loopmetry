"""Deterministic generation and text-level merging of local Codex hook config.

Pure logic only -- no filesystem access (cli.py owns reading, diffing, backup,
and the force policy, same split as hook_integration.py). Codex's hook config
is TOML with no stdlib writer, so unlike hook_integration.py (which returns a
parsed dict cli.py re-serializes), this module returns raw text: tomllib
parses/validates the whole file and each individual candidate block in
isolation (every "[[hooks.<Event>]]" occurrence is independently valid TOML on
its own, so this needs no full round-trip serializer), but writing replaces
only the located block spans, leaving every other byte -- comments, formatting,
unrelated tables -- untouched.

Codex's command field has no args array (unlike Claude Code's JSON installer):
it is a single shell-parsed string, so a project_id containing spaces or shell
metacharacters is embedded via shlex.quote() rather than passed as a separate
exec-form argument.
"""

from __future__ import annotations

import re
import shlex
import tomllib

from .hook_integration import INTEGRATION_HOOK_EVENTS as CODEX_INTEGRATION_HOOK_EVENTS

_BASE_ARGS = ["loopmetry", "capture-hook", "--source", "codex"]
_TIMEOUT = 3


def build_hook_command(project_id: str | None) -> str:
    if project_id:
        return " ".join([*_BASE_ARGS, "--project-id", shlex.quote(project_id)])
    return " ".join(_BASE_ARGS)


def _owned_command_args(command: object) -> list[str] | None:
    if not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    prefix, remainder = tokens[: len(_BASE_ARGS)], tokens[len(_BASE_ARGS) :]
    if prefix != _BASE_ARGS:
        return None
    if remainder and (len(remainder) != 2 or remainder[0] != "--project-id"):
        return None
    return tokens


def _validate_whole_file(existing_text: str) -> dict:
    try:
        parsed = tomllib.loads(existing_text) if existing_text.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"existing file is not valid TOML: {exc}") from exc
    hooks = parsed.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' must be a TOML table")
    for event in CODEX_INTEGRATION_HOOK_EVENTS:
        if event in hooks and not isinstance(hooks[event], list):
            raise ValueError(f"'hooks.{event}' must be a TOML array of tables")
    return parsed


def _block_spans(existing_text: str, event: str) -> list[tuple[int, int]]:
    """Byte spans of every bare `[[hooks.<event>]]` occurrence, each running to
    just before the next *non-continuation* header line or EOF.

    A naive "next line starting with `[`" boundary would match this block's own
    nested `[[hooks.<event>.hooks]]` sub-header and truncate the span to just
    its first two lines, before `_span_is_owned` ever sees the handler table.
    The boundary regex below excludes exactly that one continuation shape
    (`[[hooks.<event>.` ...) while still treating a *repeated* bare
    `[[hooks.<event>]]` (a second array entry) or any other table's header as a
    real boundary.
    """

    escaped_event = re.escape(event)
    header_re = re.compile(rf"(?m)^\[\[hooks\.{escaped_event}\]\][ \t]*$")
    boundary_re = re.compile(rf"(?m)^\[(?!\[hooks\.{escaped_event}\.)")
    spans: list[tuple[int, int]] = []
    for match in header_re.finditer(existing_text):
        start = match.start()
        next_header = boundary_re.search(existing_text, match.end())
        end = next_header.start() if next_header else len(existing_text)
        spans.append((start, end))
    return spans


def _span_is_owned(existing_text: str, span: tuple[int, int]) -> list[str] | None:
    start, end = span
    try:
        parsed = tomllib.loads(existing_text[start:end])
    except tomllib.TOMLDecodeError:
        return None
    hooks = parsed.get("hooks")
    if not isinstance(hooks, dict) or len(hooks) != 1:
        return None
    (entries,) = hooks.values()
    if not isinstance(entries, list) or len(entries) != 1:
        return None
    entry = entries[0]
    if not isinstance(entry, dict) or set(entry.keys()) != {"hooks"}:
        return None
    handlers = entry["hooks"]
    if not isinstance(handlers, list) or len(handlers) != 1:
        return None
    handler = handlers[0]
    if not isinstance(handler, dict) or set(handler.keys()) != {"type", "command", "timeout"}:
        return None
    if handler.get("type") != "command" or handler.get("timeout") != _TIMEOUT:
        return None
    return _owned_command_args(handler.get("command"))


def _render_block(event: str, project_id: str | None) -> str:
    command = build_hook_command(project_id)
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f"[[hooks.{event}]]\n\n"
        f"[[hooks.{event}.hooks]]\n"
        f'type = "command"\n'
        f'command = "{escaped}"\n'
        f"timeout = {_TIMEOUT}\n"
    )


def merge_config(existing_text: str, project_id: str | None) -> tuple[str, bool]:
    _validate_whole_file(existing_text)
    desired_args = shlex.split(build_hook_command(project_id))
    text = existing_text
    changed = False
    for event in CODEX_INTEGRATION_HOOK_EVENTS:
        spans = _block_spans(text, event)
        owned = [(span, _span_is_owned(text, span)) for span in spans]
        owned = [(span, args) for span, args in owned if args is not None]
        if len(owned) == 1 and owned[0][1] == desired_args:
            continue
        changed = True
        for span, _ in sorted(owned, key=lambda item: item[0][0], reverse=True):
            start, end = span
            text = text[:start] + text[end:]
        separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        text = text + separator + _render_block(event, project_id)
    return text, changed


def remove_config(existing_text: str) -> tuple[str, bool]:
    _validate_whole_file(existing_text)
    text = existing_text
    changed = False
    for event in CODEX_INTEGRATION_HOOK_EVENTS:
        spans = _block_spans(text, event)
        owned_spans = [span for span in spans if _span_is_owned(text, span) is not None]
        if not owned_spans:
            continue
        changed = True
        for start, end in sorted(owned_spans, reverse=True):
            text = text[:start] + text[end:]
    return text, changed

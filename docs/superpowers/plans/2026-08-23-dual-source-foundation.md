# Dual-Source Foundation (Milestone 2, Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the provider-neutral `SourceAdapter` contract, typed adapter models, per-event provenance, and checkpoint persistence — with the existing hook path conforming to the contract and no behavior change beyond that.

**Architecture:** Provenance becomes an optional envelope-level list on the canonical `Event` (schema `0.2`, still accepting `0.1`). A new `src/loopmetry/adapters/` package holds the contract types (`SourceAdapter`, discovery context, candidates, preview, capabilities, checkpoints, adapter runs, coverage, diagnostics) plus a `HookSourceAdapter` wrapper over today's `.loopmetry/hooks/` discovery. Backfill adapters (slice 2+) will implement the same contract.

**Tech Stack:** Python ≥3.12 stdlib only (dataclasses, StrEnum, json, pathlib, unittest). No new dependencies.

## Global Constraints

- Runtime is standard-library-only; use `uv` for all commands (`AGENTS.md`).
- Tests use `unittest`, run via `uv run python -m unittest discover -s tests -v`.
- Lint: `uvx --from ruff==0.12.12 ruff check .`
- Invariant 10 (`AGENTS.md`): imported events carry source, capture-mode, adapter-version, and coverage provenance; merged events never lose provenance; conflicts stay visible.
- Capture modes are exactly: `hook`, `history-backfill`, `explicit-import`, `deterministically-derived` (D-011).
- No source path implements metric semantics; adapters emit canonical events only.
- Existing `0.1` JSONL files (e.g. `examples/demo_project.jsonl`, prior `.loopmetry/hooks/*.jsonl`) must keep loading unchanged.
- No behavior change to hook capture output beyond adding the `provenance` field.
- Follow repo style: `from __future__ import annotations`, frozen slotted dataclasses, module-specific `ValueError` subclasses, tuple-typed collections on frozen dataclasses.

## Design references

- `docs/decision-log.md` D-011 (rationale), `docs/event-schema.md` "Provenance envelope" section (field contract), `docs/roadmap.md` milestone 2 slice 1 (scope), `docs/architecture.md` "Capture and adapter layer".
- Existing interfaces this plan builds on: `Event.from_mapping` / `Event.to_mapping` (`src/loopmetry/schema.py:97,192`), `normalize_hook_payload` and `_base_event` (`src/loopmetry/hook_capture.py:285,258`), `discover_event_files` (`src/loopmetry/workflow.py:58`), `load_jsonl` (`src/loopmetry/io.py:16`).

---

### Task 1: Provenance on the canonical Event (schema 0.2)

**Files:**
- Modify: `src/loopmetry/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: existing `Event`, `SchemaError`, `_required_text`.
- Produces (later tasks rely on these exact names):
  - `CaptureMode(StrEnum)` with members `HOOK = "hook"`, `HISTORY_BACKFILL = "history-backfill"`, `EXPLICIT_IMPORT = "explicit-import"`, `DETERMINISTICALLY_DERIVED = "deterministically-derived"`.
  - `ProvenanceRecord` frozen dataclass: `source: str`, `capture_mode: CaptureMode`, `adapter_version: str`, `source_ref: Mapping[str, Any] | None = None`; classmethod `from_mapping(raw) -> ProvenanceRecord`; method `to_mapping() -> dict[str, Any]`.
  - `Event.provenance: tuple[ProvenanceRecord, ...] = ()` (new optional envelope field).
  - `SCHEMA_VERSION = "0.2"`; `SUPPORTED_SCHEMA_VERSIONS = frozenset({"0.1", "0.2"})`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schema.py` (match its existing unittest style):

```python
class ProvenanceTests(unittest.TestCase):
    def _base_raw(self) -> dict:
        return {
            "event_id": "evt-1",
            "project_id": "proj",
            "session_id": "sess",
            "timestamp": "2026-08-23T10:00:00Z",
            "type": "note",
            "actor": "system",
            "source": "claude-code",
            "data": {"summary": "x"},
        }

    def test_event_without_provenance_defaults_to_empty(self) -> None:
        event = Event.from_mapping(self._base_raw())
        self.assertEqual(event.provenance, ())
        self.assertNotIn("provenance", event.to_mapping())

    def test_schema_0_1_events_still_load(self) -> None:
        raw = self._base_raw()
        raw["schema_version"] = "0.1"
        event = Event.from_mapping(raw)
        self.assertEqual(event.schema_version, "0.1")

    def test_provenance_round_trip(self) -> None:
        raw = self._base_raw()
        raw["schema_version"] = "0.2"
        raw["provenance"] = [
            {
                "source": "claude-code",
                "capture_mode": "hook",
                "adapter_version": "1.0.0",
            },
            {
                "source": "claude-code",
                "capture_mode": "history-backfill",
                "adapter_version": "1.0.0",
                "source_ref": {"session_file_sha256": "abc", "record_index": 7},
            },
        ]
        event = Event.from_mapping(raw)
        self.assertEqual(len(event.provenance), 2)
        self.assertIs(event.provenance[0].capture_mode, CaptureMode.HOOK)
        self.assertEqual(
            event.provenance[1].source_ref, {"session_file_sha256": "abc", "record_index": 7}
        )
        self.assertEqual(event.to_mapping()["provenance"], raw["provenance"])

    def test_invalid_capture_mode_is_rejected(self) -> None:
        raw = self._base_raw()
        raw["provenance"] = [
            {"source": "x", "capture_mode": "guessed", "adapter_version": "1"}
        ]
        with self.assertRaises(SchemaError):
            Event.from_mapping(raw)

    def test_provenance_must_be_a_list_of_objects(self) -> None:
        raw = self._base_raw()
        raw["provenance"] = {"capture_mode": "hook"}
        with self.assertRaises(SchemaError):
            Event.from_mapping(raw)

    def test_unsupported_schema_version_is_rejected(self) -> None:
        raw = self._base_raw()
        raw["schema_version"] = "0.3"
        with self.assertRaises(SchemaError):
            Event.from_mapping(raw)
```

Add `CaptureMode` to the imports from `loopmetry.schema` at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_schema -v`
Expected: FAIL / ERROR with `ImportError: cannot import name 'CaptureMode'`.

- [ ] **Step 3: Implement in `src/loopmetry/schema.py`**

Replace `SCHEMA_VERSION = "0.1"` with:

```python
SCHEMA_VERSION = "0.2"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"0.1", "0.2"})
```

Add after the `Actor` enum:

```python
class CaptureMode(StrEnum):
    HOOK = "hook"
    HISTORY_BACKFILL = "history-backfill"
    EXPLICIT_IMPORT = "explicit-import"
    DETERMINISTICALLY_DERIVED = "deterministically-derived"


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    """How one observation of an event was obtained (D-011)."""

    source: str
    capture_mode: CaptureMode
    adapter_version: str
    source_ref: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, raw: Any) -> "ProvenanceRecord":
        if not isinstance(raw, Mapping):
            raise SchemaError("provenance entries must be JSON objects")
        try:
            capture_mode = CaptureMode(_required_text(raw.get("capture_mode"), "capture_mode"))
        except ValueError as exc:
            allowed = ", ".join(member.value for member in CaptureMode)
            raise SchemaError(f"unknown capture_mode; expected one of: {allowed}") from exc
        source_ref = raw.get("source_ref")
        if source_ref is not None and not isinstance(source_ref, Mapping):
            raise SchemaError("provenance source_ref must be a JSON object")
        return cls(
            source=_required_text(raw.get("source"), "provenance.source"),
            capture_mode=capture_mode,
            adapter_version=_required_text(raw.get("adapter_version"), "adapter_version"),
            source_ref=dict(source_ref) if source_ref is not None else None,
        )

    def to_mapping(self) -> dict[str, Any]:
        mapping: dict[str, Any] = {
            "source": self.source,
            "capture_mode": self.capture_mode.value,
            "adapter_version": self.adapter_version,
        }
        if self.source_ref is not None:
            mapping["source_ref"] = dict(self.source_ref)
        return mapping
```

In `Event`, add the field after `data`:

```python
    provenance: tuple[ProvenanceRecord, ...] = ()
```

(keep `schema_version: str = SCHEMA_VERSION` as the last field so existing positional construction is unaffected — the codebase constructs events via keywords, but keep ordering anyway).

In `Event.from_mapping`, replace the version check with:

```python
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            supported = ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))
            raise SchemaError(
                f"unsupported schema_version {schema_version!r}; expected one of: {supported}"
            )
```

and parse provenance before constructing the event:

```python
        raw_provenance = raw.get("provenance", [])
        if raw_provenance is None:
            raw_provenance = []
        if not isinstance(raw_provenance, list):
            raise SchemaError("provenance must be a list of JSON objects")
        provenance = tuple(ProvenanceRecord.from_mapping(item) for item in raw_provenance)
```

then pass `provenance=provenance` to `cls(...)`.

In `Event.to_mapping`, after the `"data"` entry:

```python
        if self.provenance:
            mapping["provenance"] = [record.to_mapping() for record in self.provenance]
```

(restructure the return into a local `mapping` variable first).

- [ ] **Step 4: Run the full suite**

Run: `uv run python -m unittest discover -s tests -v`
Expected: all pass, including the 35 pre-existing tests (proves 0.1 inputs and hook capture output still load; `hook_capture._base_event` passes `"schema_version": "0.1"` explicitly, which stays supported).

- [ ] **Step 5: Update `docs/event-schema.md`**

Retitle the doc `# Canonical event schema v0.2`, change the envelope `schema_version` row to `` `0.1` or `0.2`; new events are written as `0.2` ``, retitle "Provenance envelope (planned, milestone 2)" to "Provenance envelope" and rewrite its intro to present tense (drop "will additionally carry" → "carries"; drop the final sentence about field names being finalized later). Update the compatibility section to note `0.2` adds the optional `provenance` list and that `0.1` events remain readable.

- [ ] **Step 6: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check .
git add src/loopmetry/schema.py tests/test_schema.py docs/event-schema.md
git commit -m "feat: add capture-mode provenance to canonical events (schema 0.2)"
```

---

### Task 2: Adapter contract models and SourceAdapter protocol

**Files:**
- Create: `src/loopmetry/adapters/__init__.py`
- Create: `src/loopmetry/adapters/base.py`
- Test: `tests/test_adapters.py`

**Interfaces:**
- Consumes: `Event`, `CaptureMode` from Task 1.
- Produces (slice 2+ adapters and Task 4 rely on these exact names):
  - `EVIDENCE_CATEGORIES: tuple[str, ...] = ("requirements", "plans", "file_reads", "file_changes", "commands", "verifications", "errors", "commits", "human_turns")`
  - `Coverage(StrEnum)`: `FULL="full"`, `PARTIAL="partial"`, `NONE="none"`
  - `CoverageReport` frozen dataclass: `categories: Mapping[str, Coverage]`; `from_mapping`/`to_mapping`; constructor validates category names against `EVIDENCE_CATEGORIES`.
  - `Diagnostic` frozen dataclass: `kind: str`, `summary: str`, `count: int = 1` (kinds used later: `"unparsed_record"`, `"adapter_conflict"`, `"truncated_input"`, `"checkpoint_reset"`).
  - `DiscoveryContext` frozen dataclass: `project_root: Path`, `since: datetime | None = None`, `until: datetime | None = None`, `interactive: bool = False`.
  - `SourceCandidate` frozen dataclass: `candidate_id: str`, `source: str`, `label: str`, `session_id: str | None`, `size_bytes: int`, `modified_at: datetime`, `event_estimate: int | None = None`.
  - `ImportPreview` frozen dataclass: `source: str`, `candidates: tuple[SourceCandidate, ...]`, with computed properties `total_size_bytes: int` and `session_count: int`.
  - `AdapterCapabilities` frozen dataclass: `capture_modes: tuple[CaptureMode, ...]`, `evidence_categories: tuple[str, ...]`.
  - `Checkpoint` frozen dataclass: `source: str`, `positions: Mapping[str, Mapping[str, Any]]` (candidate_id → `{"content_sha256": str, "records_read": int}`); `from_mapping`/`to_mapping`.
  - `AdapterRun` frozen dataclass: `source: str`, `adapter_version: str`, `events: tuple[Event, ...]`, `diagnostics: tuple[Diagnostic, ...]`, `coverage: CoverageReport`, `checkpoint: Checkpoint | None = None`.
  - `SourceAdapter(Protocol)` with `name: str`, `adapter_version: str`, `capabilities() -> AdapterCapabilities`, `discover(context: DiscoveryContext) -> tuple[SourceCandidate, ...]` (deterministic ordering required), `preview(candidates: Sequence[SourceCandidate]) -> ImportPreview`, `import_candidates(candidates: Sequence[SourceCandidate], context: DiscoveryContext, checkpoint: Checkpoint | None = None) -> AdapterRun`.
  - `AdapterError(ValueError)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_adapters.py`:

```python
"""Tests for the provider-neutral source-adapter contract."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from loopmetry.adapters.base import (
    EVIDENCE_CATEGORIES,
    AdapterError,
    Checkpoint,
    Coverage,
    CoverageReport,
    Diagnostic,
    DiscoveryContext,
    ImportPreview,
    SourceCandidate,
)


def _candidate(candidate_id: str, size: int) -> SourceCandidate:
    return SourceCandidate(
        candidate_id=candidate_id,
        source="claude-code",
        label="session",
        session_id=candidate_id,
        size_bytes=size,
        modified_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )


class CoverageReportTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        report = CoverageReport(categories={"commands": Coverage.FULL, "plans": Coverage.PARTIAL})
        loaded = CoverageReport.from_mapping(report.to_mapping())
        self.assertEqual(loaded.categories["commands"], Coverage.FULL)
        self.assertEqual(loaded.categories["plans"], Coverage.PARTIAL)

    def test_unknown_category_is_rejected(self) -> None:
        with self.assertRaises(AdapterError):
            CoverageReport(categories={"vibes": Coverage.FULL})

    def test_categories_are_the_documented_set(self) -> None:
        self.assertIn("verifications", EVIDENCE_CATEGORIES)
        self.assertIn("human_turns", EVIDENCE_CATEGORIES)


class ImportPreviewTests(unittest.TestCase):
    def test_totals(self) -> None:
        preview = ImportPreview(
            source="claude-code",
            candidates=(_candidate("a", 100), _candidate("b", 250)),
        )
        self.assertEqual(preview.total_size_bytes, 350)
        self.assertEqual(preview.session_count, 2)


class CheckpointTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        checkpoint = Checkpoint(
            source="claude-code",
            positions={"a": {"content_sha256": "deadbeef", "records_read": 12}},
        )
        loaded = Checkpoint.from_mapping(checkpoint.to_mapping())
        self.assertEqual(loaded.source, "claude-code")
        self.assertEqual(loaded.positions["a"]["records_read"], 12)

    def test_rejects_non_mapping_positions(self) -> None:
        with self.assertRaises(AdapterError):
            Checkpoint.from_mapping({"source": "x", "positions": [1, 2]})


class ModelBasicsTests(unittest.TestCase):
    def test_discovery_context_defaults(self) -> None:
        context = DiscoveryContext(project_root=Path("."))
        self.assertIsNone(context.since)
        self.assertFalse(context.interactive)

    def test_diagnostic_default_count(self) -> None:
        diagnostic = Diagnostic(kind="unparsed_record", summary="unknown record type")
        self.assertEqual(diagnostic.count, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_adapters -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'loopmetry.adapters'`.

- [ ] **Step 3: Implement `src/loopmetry/adapters/base.py`**

```python
"""Provider-neutral source-adapter contract (decision D-011).

Both prospective hook capture and retrospective historical backfill implement this
contract and emit the same canonical events. Adapters own provider-specific parsing
and minimization only; they never implement metric semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..schema import CaptureMode, Event

EVIDENCE_CATEGORIES: tuple[str, ...] = (
    "requirements",
    "plans",
    "file_reads",
    "file_changes",
    "commands",
    "verifications",
    "errors",
    "commits",
    "human_turns",
)


class AdapterError(ValueError):
    """Raised when adapter inputs or persisted adapter state are invalid."""


class Coverage(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Per-evidence-category coverage for one adapter run."""

    categories: Mapping[str, Coverage] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = sorted(set(self.categories) - set(EVIDENCE_CATEGORIES))
        if unknown:
            allowed = ", ".join(EVIDENCE_CATEGORIES)
            raise AdapterError(
                f"unknown coverage categories {unknown}; expected a subset of: {allowed}"
            )
        object.__setattr__(self, "categories", dict(self.categories))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CoverageReport":
        if not isinstance(raw, Mapping):
            raise AdapterError("coverage report must be a JSON object")
        categories_raw = raw.get("categories", {})
        if not isinstance(categories_raw, Mapping):
            raise AdapterError("coverage categories must be a JSON object")
        try:
            categories = {
                str(name): Coverage(str(value)) for name, value in categories_raw.items()
            }
        except ValueError as exc:
            allowed = ", ".join(member.value for member in Coverage)
            raise AdapterError(f"coverage values must be one of: {allowed}") from exc
        return cls(categories=categories)

    def to_mapping(self) -> dict[str, Any]:
        return {"categories": {name: value.value for name, value in self.categories.items()}}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A visible, content-free adapter problem report (never a silent drop)."""

    kind: str
    summary: str
    count: int = 1


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    """Bounds for candidate discovery; history is never read outside these bounds."""

    project_root: Path
    since: datetime | None = None
    until: datetime | None = None
    interactive: bool = False


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    """One importable unit (typically a session) found during bounded discovery."""

    candidate_id: str
    source: str
    label: str
    session_id: str | None
    size_bytes: int
    modified_at: datetime
    event_estimate: int | None = None


@dataclass(frozen=True, slots=True)
class ImportPreview:
    """What the user sees and confirms before any import happens."""

    source: str
    candidates: tuple[SourceCandidate, ...] = ()

    @property
    def total_size_bytes(self) -> int:
        return sum(candidate.size_bytes for candidate in self.candidates)

    @property
    def session_count(self) -> int:
        return len(self.candidates)


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    capture_modes: tuple[CaptureMode, ...]
    evidence_categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Incremental-import position per candidate, keyed by candidate_id."""

    source: str
    positions: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "positions",
            {key: dict(value) for key, value in dict(self.positions).items()},
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Checkpoint":
        if not isinstance(raw, Mapping):
            raise AdapterError("checkpoint must be a JSON object")
        source = raw.get("source")
        if not isinstance(source, str) or not source.strip():
            raise AdapterError("checkpoint source must be a non-empty string")
        positions_raw = raw.get("positions", {})
        if not isinstance(positions_raw, Mapping):
            raise AdapterError("checkpoint positions must be a JSON object")
        positions: dict[str, dict[str, Any]] = {}
        for key, value in positions_raw.items():
            if not isinstance(value, Mapping):
                raise AdapterError("each checkpoint position must be a JSON object")
            positions[str(key)] = dict(value)
        return cls(source=source.strip(), positions=positions)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "positions": {key: dict(value) for key, value in self.positions.items()},
        }


@dataclass(frozen=True, slots=True)
class AdapterRun:
    """The complete, auditable result of one adapter import."""

    source: str
    adapter_version: str
    events: tuple[Event, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    coverage: CoverageReport = field(default_factory=CoverageReport)
    checkpoint: Checkpoint | None = None


@runtime_checkable
class SourceAdapter(Protocol):
    """Contract shared by hook capture and historical backfill (D-011).

    Implementations must keep ``discover`` ordering deterministic, must respect the
    ``DiscoveryContext`` bounds, and must report unparsed input as ``Diagnostic``
    entries instead of silently dropping records.
    """

    name: str
    adapter_version: str

    def capabilities(self) -> AdapterCapabilities: ...

    def discover(self, context: DiscoveryContext) -> tuple[SourceCandidate, ...]: ...

    def preview(self, candidates: Sequence[SourceCandidate]) -> ImportPreview: ...

    def import_candidates(
        self,
        candidates: Sequence[SourceCandidate],
        context: DiscoveryContext,
        checkpoint: Checkpoint | None = None,
    ) -> AdapterRun: ...
```

Create `src/loopmetry/adapters/__init__.py`:

```python
"""Source adapters: prospective hook capture and retrospective backfill (D-011)."""

from .base import (
    EVIDENCE_CATEGORIES,
    AdapterCapabilities,
    AdapterError,
    AdapterRun,
    Checkpoint,
    Coverage,
    CoverageReport,
    Diagnostic,
    DiscoveryContext,
    ImportPreview,
    SourceAdapter,
    SourceCandidate,
)

__all__ = [
    "EVIDENCE_CATEGORIES",
    "AdapterCapabilities",
    "AdapterError",
    "AdapterRun",
    "Checkpoint",
    "Coverage",
    "CoverageReport",
    "Diagnostic",
    "DiscoveryContext",
    "ImportPreview",
    "SourceAdapter",
    "SourceCandidate",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_adapters -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check .
git add src/loopmetry/adapters tests/test_adapters.py
git commit -m "feat: add provider-neutral SourceAdapter contract and typed models"
```

---

### Task 3: Checkpoint persistence

**Files:**
- Create: `src/loopmetry/adapters/checkpoints.py`
- Test: `tests/test_adapters.py` (extend)

**Interfaces:**
- Consumes: `Checkpoint`, `AdapterError` from Task 2.
- Produces:
  - `checkpoint_path(project_root: Path, source: str) -> Path` → `<project_root>/.loopmetry/checkpoints/<source>.json` (`source` sanitized to `[a-zA-Z0-9._-]`).
  - `load_checkpoint(project_root: Path, source: str) -> Checkpoint | None` — returns `None` for missing file; raises `AdapterError` for a corrupt file (callers surface a `checkpoint_reset` diagnostic and start fresh — decided here so slice 2 imports never silently trust bad state).
  - `save_checkpoint(project_root: Path, checkpoint: Checkpoint) -> Path` — atomic (write temp file in the same directory, then `os.replace`), directory `0o700`, file `0o600`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adapters.py`:

```python
import tempfile

from loopmetry.adapters.checkpoints import (
    checkpoint_path,
    load_checkpoint,
    save_checkpoint,
)


class CheckpointPersistenceTests(unittest.TestCase):
    def test_missing_checkpoint_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_checkpoint(Path(tmp), "claude-code"))

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = Checkpoint(
                source="claude-code",
                positions={"a": {"content_sha256": "deadbeef", "records_read": 3}},
            )
            written = save_checkpoint(root, checkpoint)
            self.assertEqual(written, checkpoint_path(root, "claude-code"))
            loaded = load_checkpoint(root, "claude-code")
            assert loaded is not None
            self.assertEqual(loaded.positions["a"]["records_read"], 3)

    def test_corrupt_checkpoint_raises_adapter_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = checkpoint_path(root, "claude-code")
            path.parent.mkdir(parents=True)
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(AdapterError):
                load_checkpoint(root, "claude-code")

    def test_source_name_is_sanitized_in_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = checkpoint_path(Path(tmp), "../evil source")
            self.assertTrue(str(path).startswith(tmp))
            self.assertNotIn("..", path.name)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_adapters -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'loopmetry.adapters.checkpoints'`.

- [ ] **Step 3: Implement `src/loopmetry/adapters/checkpoints.py`**

```python
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
    return Checkpoint.from_mapping(raw)


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_adapters -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check .
git add src/loopmetry/adapters/checkpoints.py tests/test_adapters.py
git commit -m "feat: persist adapter checkpoints atomically under .loopmetry"
```

---

### Task 4: Hook path conforms to the contract

**Files:**
- Modify: `src/loopmetry/hook_capture.py`
- Create: `src/loopmetry/adapters/hook.py`
- Test: `tests/test_hook_capture.py` (extend), `tests/test_adapters.py` (extend)

**Interfaces:**
- Consumes: `ProvenanceRecord`, `CaptureMode` (Task 1); contract models (Task 2); `discover_event_files` (`src/loopmetry/workflow.py:58`); `load_jsonl` (`src/loopmetry/io.py:16`).
- Produces:
  - `hook_capture.HOOK_ADAPTER_VERSION = "1.0.0"` and every event from `normalize_hook_payload` carrying exactly one provenance record `(source=<source>, capture_mode=CaptureMode.HOOK, adapter_version=HOOK_ADAPTER_VERSION)`.
  - `adapters.hook.HookSourceAdapter` implementing `SourceAdapter` over `.loopmetry/hooks/*.jsonl` files (no new discovery surface; wraps today's narrow rules).

- [ ] **Step 1: Write the failing test for provenance on hook events**

Append to `tests/test_hook_capture.py`:

```python
class HookProvenanceTests(unittest.TestCase):
    def test_hook_events_carry_hook_provenance(self) -> None:
        events = normalize_hook_payload(
            {
                "hook_event_name": "SessionStart",
                "session_id": "sess-1",
                "cwd": ".",
            },
            source="claude-code",
        )
        self.assertEqual(len(events), 1)
        record = events[0].provenance[0]
        self.assertEqual(record.source, "claude-code")
        self.assertEqual(record.capture_mode.value, "hook")
        self.assertEqual(record.adapter_version, HOOK_ADAPTER_VERSION)
```

Import `HOOK_ADAPTER_VERSION` from `loopmetry.hook_capture` at the top of the test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_hook_capture -v`
Expected: FAIL/ERROR (`ImportError: cannot import name 'HOOK_ADAPTER_VERSION'`).

- [ ] **Step 3: Implement provenance in `src/loopmetry/hook_capture.py`**

Add near the top constants:

```python
HOOK_ADAPTER_VERSION = "1.0.0"
```

In `_base_event` (`src/loopmetry/hook_capture.py:258`), change `"schema_version": "0.1"` to `"schema_version": "0.2"` and add to the event mapping:

```python
            "provenance": [
                {
                    "source": source,
                    "capture_mode": "hook",
                    "adapter_version": HOOK_ADAPTER_VERSION,
                }
            ],
```

- [ ] **Step 4: Run test to verify it passes, plus the full suite**

Run: `uv run python -m unittest discover -s tests -v`
Expected: PASS (existing hook tests assert on `data` contents and event types, not on schema_version literals; if one asserts `schema_version == "0.1"`, update it to `"0.2"` — that is this task's intended behavior change).

- [ ] **Step 5: Write the failing tests for `HookSourceAdapter`**

Append to `tests/test_adapters.py`:

```python
import json as json_module

from loopmetry.adapters.hook import HookSourceAdapter


def _write_hook_file(root: Path, name: str, events: list[dict]) -> Path:
    hooks_dir = root / ".loopmetry" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    path = hooks_dir / name
    path.write_text(
        "".join(json_module.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return path


def _hook_event(event_id: str) -> dict:
    return {
        "schema_version": "0.2",
        "event_id": event_id,
        "project_id": "proj",
        "session_id": "sess",
        "timestamp": "2026-08-23T10:00:00Z",
        "type": "note",
        "actor": "system",
        "source": "claude-code",
        "data": {"summary": "x"},
        "provenance": [
            {"source": "claude-code", "capture_mode": "hook", "adapter_version": "1.0.0"}
        ],
    }


class HookSourceAdapterTests(unittest.TestCase):
    def test_discover_orders_deterministically_and_stays_in_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_hook_file(root, "codex.jsonl", [_hook_event("b")])
            _write_hook_file(root, "claude-code.jsonl", [_hook_event("a")])
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root)
            candidates = adapter.discover(context)
            self.assertEqual(
                [candidate.label for candidate in candidates],
                ["claude-code.jsonl", "codex.jsonl"],
            )

    def test_import_returns_events_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_hook_file(root, "claude-code.jsonl", [_hook_event("a"), _hook_event("b")])
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root)
            run = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual(len(run.events), 2)
            self.assertEqual(run.source, "hook")
            self.assertEqual(run.diagnostics, ())
            self.assertIn("commands", run.coverage.categories)

    def test_empty_project_discovers_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = HookSourceAdapter()
            candidates = adapter.discover(DiscoveryContext(project_root=Path(tmp)))
            self.assertEqual(candidates, ())
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_adapters -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'loopmetry.adapters.hook'`.

- [ ] **Step 7: Implement `src/loopmetry/adapters/hook.py`**

```python
"""SourceAdapter wrapper over Loopmetry's own hook-capture output files.

Discovery deliberately reuses the narrow rule from the participant workflow: only
Loopmetry-created files under ``.loopmetry/hooks/`` and ``.loopmetry/events/`` are
candidates. This adapter never reads vendor transcript formats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..hook_capture import HOOK_ADAPTER_VERSION
from ..io import load_jsonl
from ..schema import CaptureMode
from .base import (
    EVIDENCE_CATEGORIES,
    AdapterCapabilities,
    AdapterRun,
    Checkpoint,
    Coverage,
    CoverageReport,
    DiscoveryContext,
    ImportPreview,
    SourceCandidate,
)


class HookSourceAdapter:
    """Adapts already-normalized hook capture files to the SourceAdapter contract."""

    name = "hook"
    adapter_version = HOOK_ADAPTER_VERSION

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            capture_modes=(CaptureMode.HOOK,),
            evidence_categories=EVIDENCE_CATEGORIES,
        )

    def discover(self, context: DiscoveryContext) -> tuple[SourceCandidate, ...]:
        base = Path(context.project_root).expanduser()
        candidates: list[SourceCandidate] = []
        for directory in (base / ".loopmetry" / "hooks", base / ".loopmetry" / "events"):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.jsonl")):
                if not path.is_file():
                    continue
                stat = path.stat()
                modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                if context.since is not None and modified_at < context.since:
                    continue
                candidates.append(
                    SourceCandidate(
                        candidate_id=str(path),
                        source=self.name,
                        label=path.name,
                        session_id=None,
                        size_bytes=stat.st_size,
                        modified_at=modified_at,
                    )
                )
        return tuple(sorted(candidates, key=lambda candidate: candidate.candidate_id))

    def preview(self, candidates: Sequence[SourceCandidate]) -> ImportPreview:
        return ImportPreview(source=self.name, candidates=tuple(candidates))

    def import_candidates(
        self,
        candidates: Sequence[SourceCandidate],
        context: DiscoveryContext,
        checkpoint: Checkpoint | None = None,
    ) -> AdapterRun:
        events = []
        for candidate in candidates:
            events.extend(load_jsonl(candidate.candidate_id))
        events.sort(key=lambda event: (event.timestamp, event.event_id))
        coverage = CoverageReport(
            categories={category: Coverage.FULL for category in EVIDENCE_CATEGORIES}
        )
        return AdapterRun(
            source=self.name,
            adapter_version=self.adapter_version,
            events=tuple(events),
            coverage=coverage,
        )
```

Add `HookSourceAdapter` to `src/loopmetry/adapters/__init__.py` imports and `__all__` (import from `.hook`).

- [ ] **Step 8: Run the full suite and lint**

Run: `uv run python -m unittest discover -s tests -v && uvx --from ruff==0.12.12 ruff check .`
Expected: PASS / All checks passed.

- [ ] **Step 9: Commit**

```bash
git add src/loopmetry/hook_capture.py src/loopmetry/adapters tests/test_hook_capture.py tests/test_adapters.py
git commit -m "feat: emit hook provenance and wrap hook capture as a SourceAdapter"
```

---

### Task 5: Documentation and routing updates

**Files:**
- Modify: `AGENTS.md` (routing row for "Source adapters and historical backfill")
- Modify: `docs/architecture.md` (adapter-layer section: contract now implemented for the hook path)
- Modify: `docs/roadmap.md` (slice 1 status)

**Interfaces:** none (docs only).

- [ ] **Step 1: Update the routing table**

In `AGENTS.md`, change the "Source adapters and historical backfill" row's implementation cell from `` `src/loopmetry/adapters/` (planned; hook path is `src/loopmetry/hook_capture.py` today) `` to `` `src/loopmetry/adapters/`, `src/loopmetry/hook_capture.py` `` and its tests cell to `` `tests/test_adapters.py`, `tests/test_hook_capture.py` ``.

- [ ] **Step 2: Update architecture and roadmap status lines**

In `docs/architecture.md`, in "Capture and adapter layer", change "behind one shared `SourceAdapter` contract" phrasing to note the contract lives in `src/loopmetry/adapters/base.py` and the hook path conforms via `src/loopmetry/adapters/hook.py`. In `docs/roadmap.md`, mark slice 1 as implemented: change the slice 1 bullet to start with "**Shared dual-source foundation.** *(implemented)*".

- [ ] **Step 3: Full verification and commit**

```bash
uv run python -m unittest discover -s tests -v
uvx --from ruff==0.12.12 ruff check .
uv run loopmetry run --input examples/demo_project.jsonl --assignment-id demo --submitter-id local
uv build
git add AGENTS.md docs/architecture.md docs/roadmap.md
git commit -m "docs: record dual-source foundation as implemented"
```

Expected: tests pass, lint clean, demo run succeeds (0.1 example file still loads), build succeeds.

---

## Out of scope for this plan

- Claude Code / Codex historical backfill parsing (slice 2 — see `docs/superpowers/plans/2026-08-23-claude-code-history-backfill.md`).
- `loopmetry integrate` hook installer (slice 3).
- Hybrid `--source auto` merge, `adapter_conflict` handling, and explicit-import tagging of `--input` files (slice 4). `load_event_files` (`src/loopmetry/workflow.py:76`) keeps raising on conflicting duplicate event IDs until then.
- Report source-coverage sections (slice 6).

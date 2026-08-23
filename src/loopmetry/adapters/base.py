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
        invalid = sorted(
            name for name, value in self.categories.items() if not isinstance(value, Coverage)
        )
        if invalid:
            raise AdapterError(
                f"coverage values for {invalid} must be Coverage enum members, not raw values"
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
        if not isinstance(self.source, str) or not self.source.strip():
            raise AdapterError("checkpoint source must be a non-empty string")
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

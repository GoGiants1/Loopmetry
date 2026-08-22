"""Deterministic project-level evaluation over normalized evidence events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from .schema import Event


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _score(value: float) -> float:
    return round(_clamp(value) * 100.0, 1)


def _confidence(value: float) -> float:
    return round(_clamp(value), 2)


def _status(event: Event) -> str:
    value = event.data.get("status", "")
    return value.lower() if isinstance(value, str) else ""


def _summary_text(event: Event) -> str:
    for key in ("summary", "message", "command", "path", "sha", "action"):
        value = event.data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return event.type.value


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    event_id: str
    timestamp: datetime
    event_type: str
    summary: str

    @classmethod
    def from_event(cls, event: Event, summary: str | None = None) -> "EvidenceRef":
        return cls(
            event_id=event.event_id,
            timestamp=event.timestamp,
            event_type=event.type.value,
            summary=summary or _summary_text(event),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "event_type": self.event_type,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class MetricResult:
    key: str
    title: str
    score: float
    confidence: float
    summary: str
    components: Mapping[str, float]
    evidence: tuple[EvidenceRef, ...] = ()
    gaps: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        return {
            "key": self.key,
            "title": self.title,
            "score": self.score,
            "confidence": self.confidence,
            "summary": self.summary,
            "components": dict(self.components),
            "evidence": [item.to_mapping() for item in self.evidence],
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True, slots=True)
class SteeringSignal:
    label: str
    summary: str
    confidence: float
    intervention_count: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "label": self.label,
            "summary": self.summary,
            "confidence": self.confidence,
            "intervention_count": self.intervention_count,
        }


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    event_count: int
    session_count: int
    requirement_count: int
    changed_file_count: int
    verification_count: int
    error_count: int
    commit_count: int
    started_at: datetime
    ended_at: datetime

    def to_mapping(self) -> dict[str, object]:
        return {
            "event_count": self.event_count,
            "session_count": self.session_count,
            "requirement_count": self.requirement_count,
            "changed_file_count": self.changed_file_count,
            "verification_count": self.verification_count,
            "error_count": self.error_count,
            "commit_count": self.commit_count,
            "started_at": self.started_at.isoformat().replace("+00:00", "Z"),
            "ended_at": self.ended_at.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True, slots=True)
class ProjectReport:
    project_id: str
    generated_at: datetime
    snapshot: ProjectSnapshot
    metrics: tuple[MetricResult, ...]
    steering: SteeringSignal
    measurement_gaps: tuple[str, ...] = field(default_factory=tuple)

    def metric(self, key: str) -> MetricResult:
        for metric in self.metrics:
            if metric.key == key:
                return metric
        raise KeyError(key)

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": "0.1",
            "project_id": self.project_id,
            "generated_at": self.generated_at.isoformat().replace("+00:00", "Z"),
            "snapshot": self.snapshot.to_mapping(),
            "metrics": [metric.to_mapping() for metric in self.metrics],
            "non_scored_signals": {"steering": self.steering.to_mapping()},
            "measurement_gaps": list(self.measurement_gaps),
            "disclaimer": (
                "Loopmetry evaluates recorded project evidence, not developer ability, "
                "employment suitability, or individual productivity."
            ),
        }



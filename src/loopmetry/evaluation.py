"""Deterministic project-level evaluation over normalized evidence events."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Sequence

from .schema import Event, EventType
from .evaluation_models import (
    EvidenceRef,
    MetricResult,
    ProjectReport,
    ProjectSnapshot,
    SteeringSignal,
)
from .metrics_recovery import RecoveryMetricsMixin
from .metrics_traceability import TraceabilityMetricsMixin


class ProjectEvaluator(TraceabilityMetricsMixin, RecoveryMetricsMixin):
    """Compute transparent metrics without an LLM or network access."""

    def evaluate(self, events: Iterable[Event]) -> ProjectReport:
        ordered = sorted(events, key=lambda event: (event.timestamp, event.event_id))
        if not ordered:
            raise ValueError("at least one event is required")
        projects = {event.project_id for event in ordered}
        if len(projects) != 1:
            raise ValueError("events must belong to exactly one project")

        project_id = ordered[0].project_id
        snapshot = self._snapshot(ordered)
        metrics = (
            self._traceability(ordered),
            self._verification_rigor(ordered),
            self._recovery_efficiency(ordered),
            self._change_discipline(ordered),
        )
        steering = self._steering_signal(ordered)
        gaps = self._measurement_gaps(ordered)
        return ProjectReport(
            project_id=project_id,
            generated_at=datetime.now(timezone.utc),
            snapshot=snapshot,
            metrics=metrics,
            steering=steering,
            measurement_gaps=tuple(gaps),
        )

    @staticmethod
    def _snapshot(events: Sequence[Event]) -> ProjectSnapshot:
        changed_paths = {
            path
            for event in events
            if event.type is EventType.FILE_CHANGE
            for path in event.paths
        }
        return ProjectSnapshot(
            event_count=len(events),
            session_count=len({event.session_id for event in events}),
            requirement_count=len(
                {
                    event.data.get("requirement_id")
                    for event in events
                    if event.type is EventType.REQUIREMENT
                    and isinstance(event.data.get("requirement_id"), str)
                }
            ),
            changed_file_count=len(changed_paths),
            verification_count=sum(
                event.type is EventType.VERIFICATION for event in events
            ),
            error_count=sum(event.type is EventType.ERROR for event in events),
            commit_count=sum(event.type is EventType.COMMIT for event in events),
            started_at=events[0].timestamp,
            ended_at=events[-1].timestamp,
        )

    @staticmethod
    def _sessions(events: Sequence[Event]) -> dict[str, list[Event]]:
        grouped: dict[str, list[Event]] = defaultdict(list)
        for event in events:
            grouped[event.session_id].append(event)
        return dict(grouped)


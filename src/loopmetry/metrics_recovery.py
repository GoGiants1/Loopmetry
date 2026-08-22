"""Recovery, change-discipline, and steering metric mixin."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from .schema import Event, EventType
from .evaluation_models import (
    EvidenceRef,
    MetricResult,
    SteeringSignal,
    _confidence,
    _score,
    _status,
    _summary_text,
)


class RecoveryMetricsMixin:
    def _recovery_efficiency(self, events: Sequence[Event]) -> MetricResult:
        sessions = self._sessions(events)
        errors = [event for event in events if event.type is EventType.ERROR]
        if not errors:
            return MetricResult(
                key="recovery_efficiency",
                title="Recovery Efficiency",
                score=100.0,
                confidence=0.35,
                summary=(
                    "No explicit error events were recorded. The result is provisional because "
                    "absence of error evidence is not proof of error-free work."
                ),
                components={
                    "resolution_rate": 100.0,
                    "retry_efficiency": 100.0,
                    "repeat_avoidance": 100.0,
                },
                evidence=(),
                gaps=(
                    "Adapters should emit explicit error events to raise confidence in this metric.",
                ),
            )

        recovery_pairs: list[tuple[Event, Event | None, int]] = []
        for error in errors:
            session_events = sessions[error.session_id]
            start_index = session_events.index(error)
            recovery: Event | None = None
            retries = 0
            for candidate in session_events[start_index + 1 :]:
                is_failure = (
                    candidate.type is EventType.ERROR
                    or (
                        candidate.type is EventType.VERIFICATION
                        and _status(candidate) in {"failed", "error"}
                    )
                    or (
                        candidate.type is EventType.COMMAND
                        and _status(candidate) in {"failed", "error"}
                    )
                )
                if is_failure:
                    retries += 1
                is_success = (
                    candidate.type is EventType.VERIFICATION
                    and _status(candidate) == "passed"
                ) or (
                    candidate.type is EventType.COMMAND
                    and _status(candidate) == "success"
                )
                if is_success:
                    recovery = candidate
                    break
            recovery_pairs.append((error, recovery, retries))

        resolved = [pair for pair in recovery_pairs if pair[1] is not None]
        resolution_rate = len(resolved) / len(recovery_pairs)
        retry_efficiency = (
            sum(1.0 / (1.0 + pair[2]) for pair in resolved) / len(resolved)
            if resolved
            else 0.0
        )
        signatures = []
        for event in errors:
            signature = event.data.get("code") or event.data.get("message") or event.event_id
            signatures.append(str(signature).strip().lower())
        repeat_avoidance = len(set(signatures)) / len(signatures)

        value = (
            0.55 * resolution_rate
            + 0.30 * retry_efficiency
            + 0.15 * repeat_avoidance
        )
        gaps = [
            f"{sum(pair[1] is None for pair in recovery_pairs)} error event(s) have no observed recovery."
        ] if len(resolved) < len(recovery_pairs) else []
        evidence: list[EvidenceRef] = []
        for error, recovery, _ in recovery_pairs[:3]:
            evidence.append(EvidenceRef.from_event(error))
            if recovery is not None:
                evidence.append(
                    EvidenceRef.from_event(
                        recovery, summary=f"Recovery: {_summary_text(recovery)}"
                    )
                )

        confidence = 0.4 + min(len(errors) / 5.0, 0.6)
        return MetricResult(
            key="recovery_efficiency",
            title="Recovery Efficiency",
            score=_score(value),
            confidence=_confidence(confidence),
            summary=(
                "Measures whether recorded errors are resolved, how many failed retries occur, "
                "and whether the same error signature recurs."
            ),
            components={
                "resolution_rate": _score(resolution_rate),
                "retry_efficiency": _score(retry_efficiency),
                "repeat_avoidance": _score(repeat_avoidance),
            },
            evidence=tuple(evidence),
            gaps=tuple(gaps),
        )

    def _change_discipline(self, events: Sequence[Event]) -> MetricResult:
        changes = [event for event in events if event.type is EventType.FILE_CHANGE]
        paths = [path for event in changes for path in event.paths]
        unique_paths = set(paths)
        requirement_linkage = (
            sum(bool(event.requirement_ids) for event in changes) / len(changes)
            if changes
            else 0.0
        )
        # Two edits per file is treated as a healthy convergence target. More repeated
        # edits lower the signal gradually rather than declaring iteration inherently bad.
        convergence = (
            min((2.0 * len(unique_paths)) / len(changes), 1.0) if changes else 0.0
        )
        revert_actions = {
            "revert",
            "undo",
            "rollback",
        }
        reverts = sum(
            str(event.data.get("action", "")).strip().lower() in revert_actions
            for event in changes
        )
        revert_avoidance = 1.0 - (reverts / len(changes)) if changes else 0.0

        sessions = self._sessions(events)
        changed_sessions = {
            session_id: session_events
            for session_id, session_events in sessions.items()
            if any(event.type is EventType.FILE_CHANGE for event in session_events)
        }
        delivered_sessions = sum(
            any(event.type is EventType.COMMIT for event in session_events)
            for session_events in changed_sessions.values()
        )
        delivery_completion = (
            delivered_sessions / len(changed_sessions) if changed_sessions else 0.0
        )

        value = (
            0.35 * requirement_linkage
            + 0.30 * convergence
            + 0.20 * revert_avoidance
            + 0.15 * delivery_completion
        )
        gaps: list[str] = []
        if not changes:
            gaps.append("No file-change events were recorded.")
        if changes and requirement_linkage == 0:
            gaps.append("Changes are not linked to requirements.")
        if changed_sessions and delivered_sessions < len(changed_sessions):
            gaps.append("At least one changed session has no recorded commit.")

        confidence = 0.2
        confidence += 0.45 if changes else 0.0
        confidence += 0.2 if any(event.requirement_ids for event in changes) else 0.0
        confidence += 0.15 if any(event.type is EventType.COMMIT for event in events) else 0.0
        path_counts = Counter(paths)
        evidence_events = changes[:2] + [
            event for event in events if event.type is EventType.COMMIT
        ][:1]
        repeated = [path for path, count in path_counts.items() if count > 2]
        if repeated:
            gaps.append(
                "Repeated edits exceeded the convergence target for: "
                + ", ".join(sorted(repeated)[:5])
            )
        return MetricResult(
            key="change_discipline",
            title="Change Discipline",
            score=_score(value),
            confidence=_confidence(confidence),
            summary=(
                "Measures requirement-linked change scope, edit convergence, revert avoidance, "
                "and delivery completion. Iteration itself is not treated as failure."
            ),
            components={
                "requirement_linkage": _score(requirement_linkage),
                "edit_convergence": _score(convergence),
                "revert_avoidance": _score(revert_avoidance),
                "delivery_completion": _score(delivery_completion),
            },
            evidence=tuple(EvidenceRef.from_event(event) for event in evidence_events),
            gaps=tuple(gaps),
        )

    @staticmethod
    def _steering_signal(events: Sequence[Event]) -> SteeringSignal:
        interventions = [
            event for event in events if event.type is EventType.HUMAN_INTERVENTION
        ]
        observable_actions = sum(
            event.type
            in {
                EventType.PLAN,
                EventType.FILE_READ,
                EventType.FILE_CHANGE,
                EventType.COMMAND,
                EventType.VERIFICATION,
                EventType.ERROR,
            }
            for event in events
        )
        if not interventions:
            return SteeringSignal(
                label="minimal-recorded-intervention",
                summary=(
                    "No human intervention events were recorded. This may indicate a long-leash "
                    "workflow or simply an adapter coverage gap; it is not scored as good or bad."
                ),
                confidence=0.35,
                intervention_count=0,
            )

        actions = [
            str(event.data.get("action", "")).strip().lower()
            for event in interventions
        ]
        corrective = sum(
            action in {"redirect", "reject", "stop", "scope_correction"}
            for action in actions
        )
        checkpoints = sum(
            action in {"approve", "review", "checkpoint", "accept"}
            for action in actions
        )
        density = len(interventions) / max(observable_actions, 1)
        if density >= 0.2:
            label = "interactive"
            summary = (
                "Human input is recorded frequently throughout the agent loop, indicating an "
                "interactive operating style."
            )
        elif corrective > checkpoints:
            label = "corrective"
            summary = (
                "Recorded interventions are primarily redirects or rejections, indicating "
                "course-correction at observed decision points."
            )
        elif checkpoints:
            label = "checkpoint-driven"
            summary = (
                "Human input is concentrated at approvals and reviews, indicating explicit "
                "checkpoints between autonomous work segments."
            )
        else:
            label = "light-touch"
            summary = (
                "A small number of interventions were recorded without a dominant correction or "
                "approval pattern."
            )
        return SteeringSignal(
            label=label,
            summary=summary + " This is a descriptive signal, not a quality score.",
            confidence=_confidence(0.55 + min(len(interventions) / 10.0, 0.4)),
            intervention_count=len(interventions),
        )

    @staticmethod
    def _measurement_gaps(events: Sequence[Event]) -> list[str]:
        gaps: list[str] = []
        types = {event.type for event in events}
        if EventType.REQUIREMENT not in types:
            gaps.append("No requirement declarations were recorded.")
        if EventType.PLAN not in types:
            gaps.append("No planning evidence was recorded.")
        if EventType.FILE_CHANGE not in types:
            gaps.append("No file-change evidence was recorded.")
        if EventType.VERIFICATION not in types:
            gaps.append("No verification evidence was recorded.")
        if EventType.COMMIT not in types:
            gaps.append("No commit evidence was recorded.")
        sources = {event.source for event in events}
        if len(sources) == 1 and sources == {"normalized"}:
            gaps.append(
                "Events use the generic normalized source; agent-specific adapter provenance is absent."
            )
        return gaps

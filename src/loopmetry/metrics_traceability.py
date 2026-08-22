"""Traceability and verification metric mixin."""

from __future__ import annotations

from typing import Sequence

from .schema import Event, EventType
from .evaluation_models import (
    EvidenceRef,
    MetricResult,
    _confidence,
    _score,
    _status,
)


class TraceabilityMetricsMixin:
    def _traceability(self, events: Sequence[Event]) -> MetricResult:
        sessions = self._sessions(events)
        changed_sessions = {
            session_id: session_events
            for session_id, session_events in sessions.items()
            if any(event.type is EventType.FILE_CHANGE for event in session_events)
        }
        planned = 0
        for session_events in changed_sessions.values():
            first_change = next(
                event for event in session_events if event.type is EventType.FILE_CHANGE
            )
            if any(
                event.type is EventType.PLAN and event.timestamp <= first_change.timestamp
                for event in session_events
            ):
                planned += 1
        planning_coverage = (
            planned / len(changed_sessions) if changed_sessions else 0.0
        )

        changes = [event for event in events if event.type is EventType.FILE_CHANGE]
        changed_paths = {path for event in changes for path in event.paths}
        linked_paths = {
            path
            for event in changes
            if event.requirement_ids
            for path in event.paths
        }
        requirement_linkage = (
            len(linked_paths) / len(changed_paths) if changed_paths else 0.0
        )

        changed_requirement_ids = {
            requirement_id for event in changes for requirement_id in event.requirement_ids
        }
        successful_verifications = [
            event
            for event in events
            if event.type is EventType.VERIFICATION and _status(event) == "passed"
        ]
        verified_requirement_ids = {
            requirement_id
            for event in successful_verifications
            for requirement_id in event.requirement_ids
        }
        verification_linkage = (
            len(changed_requirement_ids & verified_requirement_ids)
            / len(changed_requirement_ids)
            if changed_requirement_ids
            else 0.0
        )

        commits = [event for event in events if event.type is EventType.COMMIT]
        linked_commits = 0
        for commit in commits:
            session_events = sessions[commit.session_id]
            prior_changes = [
                event
                for event in session_events
                if event.type is EventType.FILE_CHANGE and event.timestamp <= commit.timestamp
            ]
            prior_success = [
                event
                for event in session_events
                if event.type is EventType.VERIFICATION
                and _status(event) == "passed"
                and event.timestamp <= commit.timestamp
            ]
            if prior_changes and prior_success:
                last_change = prior_changes[-1]
                if any(item.timestamp >= last_change.timestamp for item in prior_success):
                    linked_commits += 1
        delivery_linkage = linked_commits / len(commits) if commits else 0.0

        value = (
            0.25 * planning_coverage
            + 0.30 * requirement_linkage
            + 0.30 * verification_linkage
            + 0.15 * delivery_linkage
        )
        gaps: list[str] = []
        if not changed_sessions:
            gaps.append("No file-change events were recorded.")
        if not changed_requirement_ids:
            gaps.append("File changes are not linked to requirement IDs.")
        if not successful_verifications:
            gaps.append("No successful verification evidence was recorded.")
        if not commits:
            gaps.append("No delivery commit evidence was recorded.")

        confidence = 0.15
        confidence += 0.25 if changes else 0.0
        confidence += 0.25 if changed_requirement_ids else 0.0
        confidence += 0.20 if successful_verifications else 0.0
        confidence += 0.15 if commits else 0.0

        evidence_events = [
            *[event for event in events if event.type is EventType.REQUIREMENT][:1],
            *[event for event in events if event.type is EventType.PLAN][:1],
            *changes[:1],
            *successful_verifications[:1],
            *commits[:1],
        ]
        return MetricResult(
            key="traceability",
            title="Intent & Evidence Traceability",
            score=_score(value),
            confidence=_confidence(confidence),
            summary=(
                "Measures whether requirements, plans, changes, verification, and delivery "
                "form an inspectable evidence chain."
            ),
            components={
                "planning_coverage": _score(planning_coverage),
                "requirement_linkage": _score(requirement_linkage),
                "verification_linkage": _score(verification_linkage),
                "delivery_linkage": _score(delivery_linkage),
            },
            evidence=tuple(EvidenceRef.from_event(event) for event in evidence_events),
            gaps=tuple(gaps),
        )

    def _verification_rigor(self, events: Sequence[Event]) -> MetricResult:
        sessions = self._sessions(events)
        changed_sessions = {
            session_id: session_events
            for session_id, session_events in sessions.items()
            if any(event.type is EventType.FILE_CHANGE for event in session_events)
        }
        verified_after_change = 0
        final_state_passed = 0
        for session_events in changed_sessions.values():
            changes = [
                event for event in session_events if event.type is EventType.FILE_CHANGE
            ]
            last_change = changes[-1]
            later_verifications = [
                event
                for event in session_events
                if event.type is EventType.VERIFICATION
                and event.timestamp >= last_change.timestamp
            ]
            if later_verifications:
                verified_after_change += 1
                if _status(later_verifications[-1]) == "passed":
                    final_state_passed += 1

        post_change_coverage = (
            verified_after_change / len(changed_sessions) if changed_sessions else 0.0
        )
        final_state = (
            final_state_passed / len(changed_sessions) if changed_sessions else 0.0
        )

        verifications = [
            event for event in events if event.type is EventType.VERIFICATION
        ]
        completed = [
            event for event in verifications if _status(event) in {"passed", "failed", "error"}
        ]
        passed = [event for event in completed if _status(event) == "passed"]
        success_rate = len(passed) / len(completed) if completed else 0.0
        kinds = {
            str(event.data.get("kind", "unknown")).strip().lower()
            for event in verifications
        }
        breadth = min(len(kinds) / 2.0, 1.0) if kinds else 0.0

        value = (
            0.35 * post_change_coverage
            + 0.25 * success_rate
            + 0.20 * breadth
            + 0.20 * final_state
        )
        gaps: list[str] = []
        if not changed_sessions:
            gaps.append("No changed sessions are available for verification analysis.")
        if not verifications:
            gaps.append("No test, lint, build, or other verification events were recorded.")
        if changed_sessions and verified_after_change < len(changed_sessions):
            gaps.append("At least one changed session lacks post-change verification.")
        if kinds and len(kinds) == 1:
            gaps.append("Only one verification kind was observed; breadth is uncertain.")

        confidence = 0.15
        confidence += 0.35 if changed_sessions else 0.0
        confidence += 0.35 if verifications else 0.0
        confidence += min(len(verifications) / 10.0, 0.15)
        evidence_events = [
            *[
                event for event in events if event.type is EventType.FILE_CHANGE
            ][-1:],
            *verifications[-3:],
        ]
        return MetricResult(
            key="verification_rigor",
            title="Verification Rigor",
            score=_score(value),
            confidence=_confidence(confidence),
            summary=(
                "Measures whether changed work is followed by diverse, successful, and final "
                "verification evidence."
            ),
            components={
                "post_change_coverage": _score(post_change_coverage),
                "verification_success_rate": _score(success_rate),
                "verification_breadth": _score(breadth),
                "final_verified_state": _score(final_state),
            },
            evidence=tuple(EvidenceRef.from_event(event) for event in evidence_events),
            gaps=tuple(gaps),
        )


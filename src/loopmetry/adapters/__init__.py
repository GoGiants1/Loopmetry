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
from .hook import HookSourceAdapter

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
    "HookSourceAdapter",
    "ImportPreview",
    "SourceAdapter",
    "SourceCandidate",
]

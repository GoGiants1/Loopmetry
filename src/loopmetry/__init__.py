"""Loopmetry: project-level evidence evaluation for AI coding workflows."""

__version__ = "0.1.0"

from .evaluation import ProjectEvaluator, ProjectReport
from .schema import Actor, Event, EventType, SchemaError

__all__ = [
    "Actor",
    "Event",
    "EventType",
    "ProjectEvaluator",
    "ProjectReport",
    "SchemaError",
    "__version__",
]

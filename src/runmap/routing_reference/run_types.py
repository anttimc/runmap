"""Reference endpoint overlays and Python planner results."""

from dataclasses import dataclass
from typing import Literal

from runmap.routing.cost_types import (
    CostBreakdown,
    PreferenceObservations,
    ResolvedPreferences,
    TraversalRef,
)
from runmap.routing.graph import GraphData
from runmap.routing_reference.cost_types import PhysicalPart
from runmap.routing_reference.search import PathResult


@dataclass(frozen=True)
class RunOverlay:
    graph: GraphData
    start: str
    destination: str
    parts: dict[int, PhysicalPart]


@dataclass(frozen=True)
class RunLeg:
    path: PathResult
    costs: CostBreakdown
    traversals: tuple[TraversalRef, ...]
    observations: PreferenceObservations


@dataclass(frozen=True)
class RunCandidate:
    outbound_left: bool | None
    legs: tuple[RunLeg, ...]
    distance_m: float
    repeated_distance_m: float


@dataclass(frozen=True)
class RunResult:
    overlay: RunOverlay
    candidates: tuple[RunCandidate, ...]
    outcomes: tuple[Literal["complete", "unreachable", "deadline"], ...]
    elapsed_s: float
    preferences: ResolvedPreferences
    reached_frontier: frozenset[int] = frozenset()

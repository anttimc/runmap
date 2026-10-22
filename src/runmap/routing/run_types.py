"""Data shared by endpoint overlays, cost preparation, and run results."""

from dataclasses import dataclass
from typing import Literal

from runmap.routing.search import GraphData, PathResult
from runmap.routing.cost_types import CostBreakdown, PhysicalPart, PreferenceObservations, ResolvedPreferences, TraversalRef


@dataclass(frozen=True)
class RunSettings:
    avoidance_ratio: float = 0.18
    centre_enabled: bool = True
    radius_ratio: float = 0.15
    transition_ratio: float = 0.05
    side_strength: float = 2.0
    reuse_strength: float = 4.0


@dataclass(frozen=True)
class RunOverlay:
    graph: GraphData
    home: str
    turnaround: str
    parts: dict[int, PhysicalPart]


@dataclass(frozen=True)
class RunCandidate:
    outbound_left: bool
    outbound: PathResult
    returning: PathResult
    distance_m: float
    repeated_distance_m: float
    outbound_costs: CostBreakdown
    returning_costs: CostBreakdown
    outbound_traversals: tuple[TraversalRef, ...]
    returning_traversals: tuple[TraversalRef, ...]
    outbound_observations: PreferenceObservations
    returning_observations: PreferenceObservations


@dataclass(frozen=True)
class RunResult:
    overlay: RunOverlay
    candidates: tuple[RunCandidate, ...]
    outcomes: tuple[Literal["complete", "unreachable", "deadline"], ...]
    elapsed_s: float
    preferences: ResolvedPreferences
    reached_frontier: frozenset[int] = frozenset()

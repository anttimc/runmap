"""Loop-planning records keep internal ranking separate from published routes."""

from dataclasses import dataclass
from typing import NotRequired, TypedDict

from runmap.routing.types import EdgeProfile, RouteMetrics


class LoopRequest(TypedDict):
    target_distance_m: float
    distance_tolerance_m: float
    max_repeated_fraction: float
    mode: NotRequired[str]
    minimize: NotRequired[str]
    allow_immediate_reversal_for_access_stem: NotRequired[bool]


class StairPolicy(TypedDict, total=False):
    mode: str


class LoopSettings(TypedDict, total=False):
    anchor_count: int
    alternative_count: int
    ascent_costs_per_m: list[float]
    stairs_multiplier: float
    overlap_factors: list[float]
    edge_profile: EdgeProfile
    stairs: StairPolicy


@dataclass(frozen=True)
class Anchor:
    node_id: str
    distance_m: float
    bearing_degrees: float


@dataclass(frozen=True)
class Assessment:
    distance_violation_m: float
    repeated_fraction_violation: float
    feasible: bool


class AnchorInfo(TypedDict):
    node_id: str
    shortest_distance_from_start_m: float
    bearing_degrees: float
    longitude: float
    latitude: float


class GenerationInfo(TypedDict):
    ascent_cost_per_m: float
    stairs_multiplier: float
    return_overlap_factor: float
    outbound_cost: float
    return_cost: float


class ConstraintViolations(TypedDict):
    distance_m: float
    repeated_fraction: float


class LoopCandidate(TypedDict):
    anchor: AnchorInfo
    generation: GenerationInfo
    outbound_arc_ids: list[str]
    return_arc_ids: list[str]
    metrics: RouteMetrics
    feasible: NotRequired[bool]
    constraint_violations: NotRequired[ConstraintViolations]
    id: NotRequired[str]


Rank = tuple[float, ...]


@dataclass(frozen=True)
class RankedCandidate:
    candidate: LoopCandidate
    rank: Rank


class LoopResult(TypedDict):
    schema_version: int
    method: str
    start_node_id: str
    request: LoopRequest
    settings: LoopSettings
    anchor_count: int
    evaluated_candidate_count: int
    feasible_candidate_count: int
    boundary_blocked_segment_count: int
    alternatives: list[LoopCandidate]

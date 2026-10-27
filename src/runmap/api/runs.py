"""Bounded run generation using the same dataset and policy as exploration."""

import math
from threading import BoundedSemaphore
from time import monotonic
from typing import Literal, Self

from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict, model_validator
from shapely.geometry import LineString, Point, Polygon

from runmap.api.paths import PathData, path_policy_index
from runmap.paths.exploration import Snap, snap_candidates
from runmap.routing.run_planner import RunSettings, plan_run, projected_points
from runmap.routing.cost_preparation import effective_run_settings, resolve_shape_strengths
from runmap.routing.cost_settings import (
    COST_MODEL_VERSION, COST_UNITS, preference_capabilities, resolve_preferences,
)
from runmap.routing.cost_types import (
    CostBreakdown, PreferenceCapabilities, PreferenceLevel, PreferenceObservations, PreferenceSettings,
    PreferenceWeightTables, ResolvedPreferences, ShapeStrengths,
)
from runmap.routing.search import route_coordinates
from runmap.routing.run_types import RunMode, RunResult
from runmap.routing.hill_types import HillModel
from runmap.routing.types import Bounds
from runmap.routing.deadlines import check_deadline

RUN_SLOTS = BoundedSemaphore(2)


class EndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-85, le=85)
    segment_id: str | None = Field(None, max_length=200)


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    avoidance_ratio: float = Field(.18, ge=0, le=1)
    centre_enabled: bool = True
    radius_ratio: float = Field(.15, ge=.02, le=1)
    transition_ratio: float = Field(.05, ge=.005, le=1)
    side_strength: float = Field(2, ge=0, le=100)
    reuse_strength: float = Field(4, ge=0, le=100)


class PreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hills: PreferenceLevel = "off"
    signals: PreferenceLevel = "off"
    busy_roads: PreferenceLevel = "off"
    greenery: PreferenceLevel = "off"


class CostConfigurationResponse(BaseModel):
    model_version: str = COST_MODEL_VERSION
    units: str = COST_UNITS
    shape_settings: SettingsRequest
    shape_strengths: ShapeStrengths
    preferences: ResolvedPreferences
    capabilities: PreferenceCapabilities
    hill_model: HillModel | None = None


class PlannerConfiguration(BaseModel):
    available: bool = True
    modes: tuple[RunMode, ...] = ("one_way", "round_trip")
    defaults: SettingsRequest = Field(default_factory=SettingsRequest)
    preference_defaults: PreferencesRequest = Field(default_factory=PreferencesRequest)
    cost_model_version: str = COST_MODEL_VERSION
    cost_units: str = COST_UNITS
    preference_capabilities: PreferenceCapabilities = Field(default_factory=preference_capabilities)
    preference_weight_tables: PreferenceWeightTables = Field(default_factory=PreferenceWeightTables)
    deadline_s: float
    hill_model: HillModel | None = None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    dataset_id: str = Field(max_length=100)
    mode: RunMode = "round_trip"
    home: EndpointRequest
    turnaround: EndpointRequest
    settings: SettingsRequest = Field(default_factory=SettingsRequest)
    preferences: PreferencesRequest = Field(default_factory=PreferencesRequest)
    radius_m: float = Field(50, gt=0, le=200)
    surface: Literal["all", "surfaced"] = "all"
    avoid_stairs: bool = True


class TraversalResponse(BaseModel):
    segment_id: str
    start_m: float
    end_m: float


class LegResponse(BaseModel):
    coordinates: list[list[float]]
    distance_m: float
    cost: float
    cost_breakdown: CostBreakdown
    observations: PreferenceObservations = Field(default_factory=PreferenceObservations)
    expanded_nodes: int
    traversals: list[TraversalResponse] = Field(default_factory=list)


class CandidateResponse(BaseModel):
    mode: RunMode
    legs: list[LegResponse] = Field(min_length=1, max_length=2)
    outbound_left: bool | None = None
    distance_m: float
    repeated_distance_m: float
    closest_centre_m: float | None = None
    encloses_centre: bool | None = None
    equivalent_to: int | None = None

    @model_validator(mode="after")
    def validate_route_mode(self) -> Self:
        expected = 1 if self.mode == "one_way" else 2
        if len(self.legs) != expected:
            raise ValueError("Leg count must match route mode")
        if self.mode == "round_trip" and (self.outbound_left is None or self.closest_centre_m is None):
            raise ValueError("Round-trip routes require shape diagnostics")
        if self.mode == "one_way" and any(value is not None for value in (
                self.outbound_left, self.closest_centre_m, self.encloses_centre)):
            raise ValueError("One-way routes cannot have round-trip shape diagnostics")
        return self


class RunSearchAttempt(BaseModel):
    bounds_epsg3067: Bounds
    nodes: int = 0
    segments: int = 0
    reason: str = "settled"
    reached_frontier: bool = False
    near_boundary: bool = False
    source_coverage_limited: bool = False


class RunSearchMetadata(BaseModel):
    attempts: list[RunSearchAttempt] = Field(default_factory=list)
    retries: int = 0
    area_limited_reason: str | None = None
    optimality_note: str = "Boundary expansion is heuristic; a better route outside the loaded area can remain undetected."


class RunResponse(BaseModel):
    mode: RunMode = "round_trip"
    dataset_id: str
    cost_model: CostConfigurationResponse
    status: Literal["complete", "unreachable", "deadline", "no_eligible_snap", "area_limited"]
    home: Snap | None = None
    turnaround: Snap | None = None
    candidates: list[CandidateResponse] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    elapsed_s: float = 0
    coverage_note: str = "Routes use the prepared network; missing coverage can omit better connections."
    search: RunSearchMetadata | None = None

    @model_validator(mode="after")
    def validate_candidate_modes(self) -> Self:
        if any(candidate.mode != self.mode for candidate in self.candidates):
            raise ValueError("Candidate modes must match response mode")
        return self


def generate_run(data: PathData, options: RunRequest, timeout_s: float = 15) -> RunResponse:
    """Use sync API workers and reject excess work instead of building a queue."""
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("Run deadline must be positive and finite")
    if not RUN_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Run planner is busy; try again shortly")
    try:
        return _generate_run(data, options, monotonic() + timeout_s)
    finally:
        RUN_SLOTS.release()


def _generate_run(data: PathData, options: RunRequest, deadline: float) -> RunResponse:
    started = monotonic()
    settings = effective_run_settings(RunSettings(**options.settings.model_dump()), options.mode)
    preferences = PreferenceSettings(**options.preferences.model_dump())
    cost_model = CostConfigurationResponse(
        shape_settings=options.settings.model_copy(update={"avoidance_ratio": settings.avoidance_ratio}), shape_strengths=resolve_shape_strengths(settings),
        preferences=resolve_preferences(preferences, hills=data.hills), capabilities=preference_capabilities(data.hills),
        hill_model=data.hills.model if data.hills else None,
    )
    index = path_policy_index(data, options.surface, options.avoid_stairs)
    home, _ = snap_candidates(data, index, options.home.longitude, options.home.latitude,
                              options.radius_m, options.home.segment_id)
    turnaround, _ = snap_candidates(data, index, options.turnaround.longitude, options.turnaround.latitude,
                                    options.radius_m, options.turnaround.segment_id)
    response = RunResponse(dataset_id=data.dataset_id, mode=options.mode, cost_model=cost_model,
                           status="no_eligible_snap", home=home, turnaround=turnaround)
    if home is None or turnaround is None:
        return response
    try:
        result = plan_run(index, home, turnaround, settings, deadline, preferences=preferences, hills=data.hills, mode=options.mode)
    except TimeoutError:
        return response.model_copy(update={"status": "deadline", "elapsed_s": monotonic() - started})
    return render_run_result(response, result, started)


def candidate_coordinates(legs: list[LegResponse]) -> list[list[float]]:
    return [point for index, leg in enumerate(legs) for point in (leg.coordinates[1:] if index else leg.coordinates)]


def candidate_response(response: RunResponse, legs: list[LegResponse],
                       outbound_left: bool | None, repeated_distance_m: float) -> CandidateResponse:
    """Share geometry diagnostics between reference and numeric routes."""
    distance = sum(leg.distance_m for leg in legs)
    line = LineString(projected_points(candidate_coordinates(legs)))
    closest, enclosed = None, None
    if response.mode == "round_trip":
        assert response.home is not None and response.turnaround is not None
        a, b = projected_points([[s.longitude, s.latitude] for s in (response.home, response.turnaround)])
        centre = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        closest = line.distance(centre)
        enclosed = Polygon(line.coords).contains(centre) if line.is_simple and line.is_ring and closest > .01 else None
    equivalent = next((i for i, prior in enumerate(response.candidates)
        if abs(prior.distance_m - distance) < .01 and line.equals(LineString(
            projected_points(candidate_coordinates(prior.legs))))), None)
    return CandidateResponse(mode=response.mode, legs=legs, outbound_left=outbound_left,
        distance_m=distance, repeated_distance_m=repeated_distance_m,
        closest_centre_m=closest, encloses_centre=enclosed, equivalent_to=equivalent)


def render_run_result(response: RunResponse, result: RunResult, started: float,
                      deadline: float | None = None) -> RunResponse:
    """Serialize complete routes using their ordered physical traversals."""
    graph = result.overlay.graph
    for candidate in result.candidates:
        check_deadline(deadline)
        legs: list[LegResponse] = []
        for leg in candidate.legs:
            traversals = [TraversalResponse(segment_id=t.segment_id,
                start_m=t.graph_end_m if t.reverse else t.graph_start_m,
                end_m=t.graph_start_m if t.reverse else t.graph_end_m) for t in leg.traversals]
            legs.append(LegResponse(coordinates=[list(p) for p in route_coordinates(graph, leg.path.arc_ids)],
                distance_m=sum(graph.arcs[a]["length_m"] for a in leg.path.arc_ids),
                cost=leg.path.cost, expanded_nodes=leg.path.expanded_nodes, cost_breakdown=leg.costs,
                observations=leg.observations, traversals=traversals))
        response.candidates.append(candidate_response(response, legs, candidate.outbound_left, candidate.repeated_distance_m))
    response.status = "deadline" if "deadline" in result.outcomes else ("complete" if result.candidates else "unreachable")
    response.outcomes = list(result.outcomes)
    response.elapsed_s = monotonic() - started
    return response

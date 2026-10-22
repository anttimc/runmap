"""Bounded run generation using the same dataset and policy as exploration."""

import math
from threading import BoundedSemaphore
from time import monotonic
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict
from shapely.geometry import LineString, Point, Polygon

from runmap.api.paths import PathData, path_policy_index
from runmap.paths.exploration import Snap, snap_candidates
from runmap.routing.run_planner import RunSettings, plan_run, projected_points
from runmap.routing.cost_preparation import resolve_shape_strengths
from runmap.routing.cost_settings import (
    COST_MODEL_VERSION, COST_UNITS, preference_capabilities, resolve_preferences,
)
from runmap.routing.cost_types import (
    CostBreakdown, PreferenceCapabilities, PreferenceLevel, PreferenceObservations, PreferenceSettings,
    PreferenceWeightTables, ResolvedPreferences, ShapeStrengths,
)
from runmap.routing.search import route_coordinates
from runmap.routing.run_types import RunResult
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
    outbound_left: bool
    outbound: LegResponse
    returning: LegResponse
    distance_m: float
    repeated_distance_m: float
    closest_centre_m: float
    encloses_centre: bool | None
    equivalent_to: int | None = None


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
    settings = RunSettings(**options.settings.model_dump())
    preferences = PreferenceSettings(**options.preferences.model_dump())
    cost_model = CostConfigurationResponse(
        shape_settings=options.settings, shape_strengths=resolve_shape_strengths(settings),
        preferences=resolve_preferences(preferences, hills=data.hills), capabilities=preference_capabilities(data.hills),
        hill_model=data.hills.model if data.hills else None,
    )
    index = path_policy_index(data, options.surface, options.avoid_stairs)
    home, _ = snap_candidates(data, index, options.home.longitude, options.home.latitude,
                              options.radius_m, options.home.segment_id)
    turnaround, _ = snap_candidates(data, index, options.turnaround.longitude, options.turnaround.latitude,
                                    options.radius_m, options.turnaround.segment_id)
    response = RunResponse(dataset_id=data.dataset_id, cost_model=cost_model,
                           status="no_eligible_snap", home=home, turnaround=turnaround)
    if home is None or turnaround is None:
        return response
    try:
        result = plan_run(index, home, turnaround, settings, deadline, preferences=preferences, hills=data.hills)
    except TimeoutError:
        return response.model_copy(update={"status": "deadline", "elapsed_s": monotonic() - started})
    return render_run_result(response, result, started)


def render_run_result(response: RunResponse, result: RunResult, started: float,
                        deadline: float | None = None) -> RunResponse:
    """Serialize complete candidate legs using the same physical traversal semantics."""
    home, turnaround = response.home, response.turnaround
    assert home is not None and turnaround is not None
    graph = result.overlay.graph
    home_point, end_point = projected_points([[home.longitude, home.latitude], [turnaround.longitude, turnaround.latitude]])
    centre = Point((home_point[0] + end_point[0]) / 2, (home_point[1] + end_point[1]) / 2)
    for candidate in result.candidates:
        check_deadline(deadline)
        legs = [LegResponse(coordinates=[list(p) for p in route_coordinates(graph, leg.arc_ids)],
                            distance_m=sum(graph.arcs[a]["length_m"] for a in leg.arc_ids),
                            cost=leg.cost, expanded_nodes=leg.expanded_nodes, cost_breakdown=costs,
                            observations=observations)
                for leg, costs, observations in ((candidate.outbound, candidate.outbound_costs, candidate.outbound_observations),
                                                 (candidate.returning, candidate.returning_costs, candidate.returning_observations))]
        points = projected_points(legs[0].coordinates + legs[1].coordinates[1:])
        line = LineString(points)
        simple = line.is_simple and line.is_ring
        closest = line.distance(centre)
        enclosed = Polygon(points).contains(centre) if simple and closest > .01 else None
        for leg_response, traversals in zip(legs, (candidate.outbound_traversals, candidate.returning_traversals)):
            for traversal in traversals:
                start, end = traversal.graph_start_m, traversal.graph_end_m
                if traversal.reverse:
                    start, end = end, start
                leg_response.traversals.append(TraversalResponse(segment_id=traversal.segment_id,
                                                                 start_m=start, end_m=end))
        equivalent = next((i for i, prior in enumerate(response.candidates)
                           if abs(prior.distance_m - candidate.distance_m) < .01
                           and line.equals(LineString(projected_points(
                               prior.outbound.coordinates + prior.returning.coordinates[1:])))), None)
        response.candidates.append(CandidateResponse(outbound_left=candidate.outbound_left,
                                   outbound=legs[0], returning=legs[1], distance_m=candidate.distance_m,
                                   repeated_distance_m=candidate.repeated_distance_m,
                                   closest_centre_m=closest, encloses_centre=enclosed, equivalent_to=equivalent))
    response.status = "deadline" if "deadline" in result.outcomes else ("complete" if result.candidates else "unreachable")
    response.outcomes = list(result.outcomes)
    response.elapsed_s = monotonic() - started
    return response

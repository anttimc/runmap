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
from runmap.routing.profiles import arc_forward
from runmap.routing.search import route_coordinates

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


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    dataset_id: str = Field(max_length=100)
    home: EndpointRequest
    turnaround: EndpointRequest
    settings: SettingsRequest = Field(default_factory=SettingsRequest)
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


class RunResponse(BaseModel):
    dataset_id: str
    status: Literal["complete", "unreachable", "deadline", "no_eligible_snap"]
    home: Snap | None = None
    turnaround: Snap | None = None
    candidates: list[CandidateResponse] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    elapsed_s: float = 0
    coverage_note: str = "Routes use the prepared network; missing coverage can omit better connections."


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
    index = path_policy_index(data, options.surface, options.avoid_stairs)
    home, _ = snap_candidates(data, index, options.home.longitude, options.home.latitude,
                              options.radius_m, options.home.segment_id)
    turnaround, _ = snap_candidates(data, index, options.turnaround.longitude, options.turnaround.latitude,
                                    options.radius_m, options.turnaround.segment_id)
    response = RunResponse(dataset_id=data.dataset_id, status="no_eligible_snap", home=home, turnaround=turnaround)
    if home is None or turnaround is None:
        return response
    try:
        result = plan_run(index, home, turnaround, RunSettings(**options.settings.model_dump()), deadline)
    except TimeoutError:
        return response.model_copy(update={"status": "deadline", "elapsed_s": monotonic() - started})
    graph = result.overlay.graph
    home_point, end_point = projected_points([[home.longitude, home.latitude], [turnaround.longitude, turnaround.latitude]])
    centre = Point((home_point[0] + end_point[0]) / 2, (home_point[1] + end_point[1]) / 2)
    for candidate in result.candidates:
        legs = [LegResponse(coordinates=[list(p) for p in route_coordinates(graph, leg.arc_ids)],
                            distance_m=sum(graph.arcs[a]["length_m"] for a in leg.arc_ids),
                            cost=leg.cost, expanded_nodes=leg.expanded_nodes)
                for leg in (candidate.outbound, candidate.returning)]
        points = projected_points(legs[0].coordinates + legs[1].coordinates[1:])
        line = LineString(points)
        simple = line.is_simple and line.is_ring
        closest = line.distance(centre)
        enclosed = Polygon(points).contains(centre) if simple and closest > .01 else None
        for leg_response, leg in zip(legs, (candidate.outbound, candidate.returning)):
            for arc_id in leg.arc_ids:
                arc = graph.arcs[arc_id]
                segment = graph.segments[arc["segment_id"]]
                part = result.overlay.parts.get(segment["id"])
                start, end = (part.start_m, part.end_m) if part else (0.0, segment["length_m"])
                if not arc_forward(segment, arc):
                    start, end = end, start
                leg_response.traversals.append(TraversalResponse(segment_id=part.segment_id if part else segment["id"],
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

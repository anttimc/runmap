"""JSON reference planning and serialization into the shared API contract."""

import math
from time import monotonic

from fastapi import HTTPException

from runmap.api import runs
from runmap.api.paths import PathData, path_policy_index
from runmap.api.runs import (
    CostConfigurationResponse,
    LegResponse,
    RunRequest,
    RunResponse,
    TraversalResponse,
    candidate_response,
)
from runmap.paths.exploration import snap_candidates
from runmap.routing.cost_settings import preference_capabilities, resolve_preferences
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.deadlines import check_deadline
from runmap.routing.run_types import RunSettings
from runmap.routing.settings import effective_run_settings, resolve_shape_strengths
from runmap.routing_reference.run_planner import plan_run
from runmap.routing_reference.run_types import RunResult
from runmap.routing_reference.search import route_coordinates


def generate_run(data: PathData, options: RunRequest, timeout_s: float = 15) -> RunResponse:
    """Use sync API workers and reject excess work instead of building a queue."""
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("Run deadline must be positive and finite")
    if not runs.RUN_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Run planner is busy; try again shortly")
    try:
        return _generate_run(data, options, monotonic() + timeout_s)
    finally:
        runs.RUN_SLOTS.release()


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

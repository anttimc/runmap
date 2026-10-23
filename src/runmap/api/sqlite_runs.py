"""Plan complete runs on expanding SQLite windows under one request deadline."""

from dataclasses import dataclass
import math
from time import monotonic

from fastapi import HTTPException

from runmap.api import runs
from runmap.api.runs import (CostConfigurationResponse, RunRequest, RunResponse, RunSearchAttempt,
                             RunSearchMetadata, render_run_result)
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.numeric_runs import load_route_segments, render_numeric_result, route_near_boundary
from runmap.routing.numeric_planner import numeric_plan_run
from runmap.api.sqlite_paths import database_snap
from runmap.paths.coverage import near_coverage_boundary
from runmap.paths.database import count_network_segments
from runmap.paths.exploration import Snap
from runmap.paths.local_network import LocalNetwork, load_local_network
from runmap.routing.cost_preparation import resolve_shape_strengths
from runmap.routing.cost_settings import prepared_preference_capabilities, resolve_prepared_preferences
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.deadlines import check_deadline
from runmap.routing.geometry import indexed_segment_points, projected_points
from runmap.routing.integer_graph import integer_arc
from runmap.routing.profiles import exploration_profile
from runmap.routing.run_planner import plan_run
from runmap.routing.run_types import RunResult, RunSettings
from runmap.routing.types import Bounds


@dataclass(frozen=True)
class RunAreaLimits:
    max_area_km2: float = 2500
    max_segments: int = 650000
    max_retries: int = 5
    minimum_dimension_m: float = 1000
    boundary_margin_m: float = 100


def validate_run_limits(limits: RunAreaLimits) -> None:
    if (not all(math.isfinite(value) and value > 0 for value in (
            limits.max_area_km2, limits.minimum_dimension_m, limits.boundary_margin_m))
            or limits.max_segments <= 0 or limits.max_retries < 0):
        raise ValueError("Invalid run planning area limits")


def generate_database_run(data: SQLitePathData, options: RunRequest, timeout_s: float = 15,
                            limits: RunAreaLimits = RunAreaLimits()) -> RunResponse:
    validate_run_limits(limits)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("Run deadline must be positive and finite")
    if not runs.RUN_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Run planner is busy; try again shortly")
    try:
        return _generate_run(data, options, monotonic(), timeout_s, limits)
    finally:
        runs.RUN_SLOTS.release()


def _generate_run(data: SQLitePathData, options: RunRequest, started: float,
                   timeout_s: float, limits: RunAreaLimits) -> RunResponse:
    deadline = started + timeout_s
    settings = RunSettings(**options.settings.model_dump())
    preferences = PreferenceSettings(**options.preferences.model_dump())
    capabilities = prepared_preference_capabilities(data.metadata.models.hill_model, data.metadata.hill_valid_fraction)
    response = RunResponse(dataset_id=data.dataset_id, status="no_eligible_snap", search=RunSearchMetadata(),
        cost_model=CostConfigurationResponse(shape_settings=options.settings,
            shape_strengths=resolve_shape_strengths(settings),
            preferences=resolve_prepared_preferences(preferences, capabilities), capabilities=capabilities,
            hill_model=data.metadata.models.hill_model))
    try:
        response.home = _snap(data, options, True, deadline)
        response.turnaround = _snap(data, options, False, deadline)
        if response.home is None or response.turnaround is None:
            response.elapsed_s = monotonic() - started
            return response
        points = projected_points([[snap.longitude, snap.latitude] for snap in (response.home, response.turnaround)])
        bounds = initial_run_bounds(points, limits.minimum_dimension_m)
        planner = _plan_numeric_windows if data.numeric is not None else _plan_windows
        return planner(data, options, response, settings, preferences, bounds, started, deadline, limits)
    except TimeoutError:
        if response.search and response.search.attempts:
            response.search.attempts[-1].reason = "deadline"
        return response.model_copy(update={"status": "deadline", "elapsed_s": monotonic() - started})


def _snap(data: SQLitePathData, options: RunRequest, home: bool, deadline: float) -> Snap | None:
    endpoint = options.home if home else options.turnaround
    result = database_snap(data, endpoint.longitude, endpoint.latitude, options.surface,
                           options.avoid_stairs, options.radius_m, endpoint.segment_id, deadline=deadline)
    return Snap(**result["snap"]) if result["snap"] is not None else None


def initial_run_bounds(points: list[tuple[float, float]], minimum_dimension_m: float) -> Bounds:
    """Bound the endpoint-diameter circle with 50% margin and a metric floor."""
    xs, ys = zip(*points)
    x, y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    half_side = max(math.hypot(max(xs) - min(xs), max(ys) - min(ys)) * 1.5,
                    minimum_dimension_m) / 2
    return x - half_side, y - half_side, x + half_side, y + half_side


def _plan_windows(data: SQLitePathData, options: RunRequest, response: RunResponse, settings: RunSettings,
                    preferences: PreferenceSettings, bounds: Bounds, started: float, deadline: float,
                    limits: RunAreaLimits) -> RunResponse:
    assert response.home is not None and response.turnaround is not None and response.search is not None
    for retry in range(limits.max_retries + 1):
        check_deadline(deadline)
        attempt = RunSearchAttempt(bounds_epsg3067=bounds)
        response.search.attempts.append(attempt)
        response.search.retries = retry
        with data.pool.connection(deadline=deadline) as connection:
            if _area_exceeded(bounds, limits.max_area_km2):
                return _area_limited(response, "maximum_area", attempt, started)
            count = count_network_segments(connection, bounds, limit=limits.max_segments + 1)
            attempt.segments = count
            if count > limits.max_segments:
                return _area_limited(response, "maximum_segments", attempt, started)
            local = load_local_network(connection, bounds,
                exploration_profile(options.surface, options.avoid_stairs),
                include_terrain=preferences.hills != "off", include_measurements=False, include_coverage=False)
            attempt.nodes = len(local.routing.nodes)
            check_deadline(deadline)
            result = plan_run(local.routing, response.home, response.turnaround, settings, deadline,
                               preferences=preferences, hills=local.hills, frontier_nodes=local.frontier_indices)
            attempt.reached_frontier = bool(result.reached_frontier)
            attempt.source_coverage_limited = _source_limited(data, result, response)
            if "deadline" in result.outcomes:
                attempt.reason = "deadline"
                # Preserve completed legs from this one working set, as the
                # reference planner does. Serialization can finish after search.
                return render_run_result(response, result, started)
            reason = _retry_reason(local, result, bounds, limits, attempt, deadline)
        if reason is None:
            if attempt.source_coverage_limited:
                response.coverage_note = "Routes approach prepared source coverage edges; missing source connections may affect the result."
            return render_run_result(response, result, started, deadline)
        attempt.reason = reason
        if retry == limits.max_retries:
            return _area_limited(response, reason, attempt, started)
        west, south, east, north = bounds
        dx, dy = (east - west) / 2, (north - south) / 2
        bounds = west - dx, south - dy, east + dx, north + dy
        # Never retain old candidate legs or dense indices into the next attempt.
        del local, result
    raise AssertionError("Run planning retry loop did not run")


def _area_exceeded(bounds: Bounds, maximum_km2: float) -> bool:
    return (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) > maximum_km2 * 1e6


def _area_limited(response: RunResponse, reason: str, attempt: RunSearchAttempt, started: float) -> RunResponse:
    assert response.search is not None
    attempt.reason = reason
    response.search.area_limited_reason = reason
    response.status = "area_limited"
    response.outcomes = ["area_limited"]
    response.elapsed_s = monotonic() - started
    response.coverage_note = "Planning reached its loaded-area limits. Try closer endpoints or increase the configured planning limits."
    return response


def _retry_reason(local: LocalNetwork, result: RunResult, bounds: Bounds, limits: RunAreaLimits,
                    attempt: RunSearchAttempt, deadline: float) -> str | None:
    check_deadline(deadline)
    west, south, east, north = bounds
    margin = max(limits.boundary_margin_m, min(east - west, north - south) * .05)
    graph = result.overlay.graph
    assert graph.integer is not None
    for candidate in result.candidates:
        for leg in (candidate.outbound, candidate.returning):
            for key in leg.arc_ids:
                check_deadline(deadline)
                arc = integer_arc(graph.integer, graph.integer.arc_indices[key])
                if any(x <= west + margin or x >= east - margin or y <= south + margin or y >= north - margin
                       for x, y in indexed_segment_points(graph.projected, arc.segment)):
                    attempt.near_boundary = True
    if attempt.reached_frontier:
        return "unloaded_connections"
    if attempt.near_boundary:
        return "near_boundary"
    if len(result.candidates) < len(result.outcomes) and local.frontier_nodes:
        return "no_route"
    return None


def _source_limited(data: SQLitePathData, result: RunResult, response: RunResponse) -> bool:
    segments = {traversal.segment_id for candidate in result.candidates
                for traversal in (*candidate.outbound_traversals, *candidate.returning_traversals)}
    if any(near_coverage_boundary(data.coverage, result.overlay.graph.segments[key]["geometry"])
           for key in segments):
        return True
    assert response.home is not None and response.turnaround is not None
    return near_coverage_boundary(data.coverage, [[snap.longitude, snap.latitude]
                                                for snap in (response.home, response.turnaround)])


def _plan_numeric_windows(data: SQLitePathData, options: RunRequest, response: RunResponse, settings: RunSettings,
                          preferences: PreferenceSettings, bounds: Bounds, started: float, deadline: float,
                          limits: RunAreaLimits) -> RunResponse:
    assert data.numeric is not None and response.home is not None and response.turnaround is not None
    assert response.search is not None
    snaps = response.home, response.turnaround
    points = projected_points([[s.longitude, s.latitude] for s in snaps])
    for retry in range(limits.max_retries + 1):
        check_deadline(deadline)
        attempt = RunSearchAttempt(bounds_epsg3067=bounds)
        response.search.attempts.append(attempt)
        response.search.retries = retry
        if _area_exceeded(bounds, limits.max_area_km2):
            return _area_limited(response, "maximum_area", attempt, started)
        west, south, east, north = bounds
        with data.pool.connection(deadline=deadline) as connection:
            count = count_network_segments(connection, bounds, limit=limits.max_segments+1)
        attempt.segments = count
        if count > limits.max_segments:
            return _area_limited(response, "maximum_segments", attempt, started)
        graph, home, turnaround = data.numeric.prepare(bounds, snaps, deadline)
        attempt.nodes = graph.native.node_count
        result = numeric_plan_run(graph, home, turnaround, points[0], points[1], settings,
            profile=exploration_profile(options.surface, options.avoid_stairs), preferences=preferences, deadline=deadline)
        expired = "deadline" in result.outcomes
        with data.pool.connection(deadline=None if expired else deadline) as connection:
            segments = load_route_segments(connection, result, None if expired else deadline)
        attempt.reached_frontier = bool(len(result.frontier_node_ids))
        attempt.source_coverage_limited = (any(near_coverage_boundary(data.coverage, s["geometry"])
            for s in segments.records.values()) or near_coverage_boundary(data.coverage,
                [[s.longitude, s.latitude] for s in snaps]))
        if expired:
            attempt.reason = "deadline"
            return render_numeric_result(response, result, segments, started, hills=preferences.hills != "off")
        check_deadline(deadline)
        margin = max(limits.boundary_margin_m, min(east-west, north-south)*.05)
        attempt.near_boundary = route_near_boundary(segments, result, snaps, bounds, margin)
        reason = "unloaded_connections" if attempt.reached_frontier else ("near_boundary" if attempt.near_boundary else None)
        if reason is None and len(result.candidates) < len(result.outcomes) and graph.native.has_frontier:
            reason = "no_route"
        if reason is None:
            if attempt.source_coverage_limited:
                response.coverage_note = "Routes approach prepared source coverage edges; missing source connections may affect the result."
            return render_numeric_result(response, result, segments, started,
                hills=preferences.hills != "off", deadline=deadline)
        attempt.reason = reason
        if retry == limits.max_retries:
            return _area_limited(response, reason, attempt, started)
        dx, dy = (east-west)/2, (north-south)/2
        bounds = west-dx, south-dy, east+dx, north+dy
        del graph, result, segments
    raise AssertionError("Run planning retry loop did not run")

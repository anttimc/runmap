"""Reference planning over expanding SQLite windows when numeric routing is absent."""

from runmap.api.runs import RunRequest, RunResponse, RunSearchAttempt
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import RunAreaLimits, _area_exceeded, _area_limited
from runmap.paths.coverage import near_coverage_boundary
from runmap.paths.database import count_network_segments
from runmap.paths.local_network import LocalNetwork, load_local_network
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.deadlines import check_deadline
from runmap.routing.geometry import indexed_segment_points
from runmap.routing.integer_graph import integer_arc
from runmap.routing.profiles import exploration_profile
from runmap.routing.run_types import RunSettings
from runmap.routing.types import Bounds
from runmap.routing_reference.api import render_run_result
from runmap.routing_reference.run_planner import plan_run
from runmap.routing_reference.run_types import RunResult


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
                               preferences=preferences, hills=local.hills, frontier_nodes=local.frontier_indices, mode=options.mode)
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


def _retry_reason(local: LocalNetwork, result: RunResult, bounds: Bounds, limits: RunAreaLimits,
                    attempt: RunSearchAttempt, deadline: float) -> str | None:
    check_deadline(deadline)
    west, south, east, north = bounds
    margin = max(limits.boundary_margin_m, min(east - west, north - south) * .05)
    graph = result.overlay.graph
    assert graph.integer is not None
    for candidate in result.candidates:
        for leg in candidate.legs:
            for key in leg.path.arc_ids:
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
                for leg in candidate.legs for traversal in leg.traversals}
    if any(near_coverage_boundary(data.coverage, result.overlay.graph.segments[key]["geometry"])
           for key in segments):
        return True
    assert response.home is not None and response.turnaround is not None
    return near_coverage_boundary(data.coverage, [[snap.longitude, snap.latitude]
                                                for snap in (response.home, response.turnaround)])

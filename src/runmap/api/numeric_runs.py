"""Render native candidates by hydrating only selected physical segments."""

from collections import defaultdict
from dataclasses import dataclass
import json
import math
import sqlite3
from time import monotonic
from typing import cast

from runmap.api.runs import LegResponse, RunResponse, TraversalResponse, candidate_response
from runmap.paths.exploration import Snap
from runmap.routing.cost_types import CostBreakdown, PreferenceObservations
from runmap.routing.deadlines import check_deadline
from runmap.routing.geometry import projected_points, slice_geometry
from runmap.routing.hill_types import HillFeatures
from runmap.routing.hills import hill_observations
from runmap.routing.numeric_planner import NumericRunCandidate, NumericRunResult
from runmap.routing.numeric_search import NumericPath
from runmap.routing.types import Bounds, Segment


@dataclass(frozen=True)
class RouteSegments:
    records: dict[int, Segment]
    projected: dict[int, list[tuple[float, float]]]


def load_route_segments(connection: sqlite3.Connection, result: NumericRunResult,
                        deadline: float | None) -> RouteSegments:
    numbers = {int(s) for c in result.candidates for p in c.legs for s in p.segment_ids}
    records: dict[int, Segment] = {}
    for number in numbers:
        check_deadline(deadline)
        row = connection.execute("SELECT payload FROM segments WHERE number=?", (number,)).fetchone()
        if row is None:
            raise ValueError("Native route segment is missing from path database")
        records[number] = cast(Segment, json.loads(row[0]))
    coordinates = [point for s in records.values() for point in s["geometry"]]
    points = projected_points(coordinates) if coordinates else []
    projected: dict[int, list[tuple[float, float]]] = {}
    offset = 0
    for number, segment in records.items():
        end = offset + len(segment["geometry"])
        projected[number] = points[offset:end]
        offset = end
    return RouteSegments(records, projected)


def route_near_boundary(segments: RouteSegments, result: NumericRunResult, snaps: tuple[Snap, Snap],
                        bounds: Bounds, margin: float) -> bool:
    west, south, east, north = bounds
    spans = {(int(n), float(a), float(b)) for c in result.candidates for p in c.legs
             for n, a, b in zip(p.segment_ids, p.start_m, p.end_m)}
    for number, a, b in spans:
        segment = segments.records[number]
        points = segments.projected[number]
        if min(a, b) > 1e-7 or max(a, b) < segment["length_m"] - 1e-7:
            points = projected_points(_coordinates(segment, points, a, b, snaps))
        if any(x <= west + margin or x >= east - margin or y <= south + margin or y >= north - margin
               for x, y in points):
            return True
    return False


def _coordinates(segment: Segment, points: list[tuple[float, float]], a: float, b: float,
                 snaps: tuple[Snap, Snap]) -> list[list[float]]:
    length = segment["length_m"]
    total = sum(math.dist(x, y) for x, y in zip(points, points[1:]))
    low, high = sorted((a, b))
    if low <= 1e-7 and high >= length - 1e-7:
        geometry = [list(p) for p in segment["geometry"]]
    else:
        def chainage(offset: float) -> float:
            return next((s.chainage_m for s in snaps if s.segment_id == segment["id"]
                         and abs(s.graph_offset_m-offset) < 1e-7), offset / length * total)
        geometry = [list(p) for p in slice_geometry(segment["geometry"], chainage(low), chainage(high))]
    for position, offset in ((0, low), (-1, high)):
        snap = next((s for s in snaps if s.segment_id == segment["id"]
                     and abs(s.graph_offset_m-offset) < 1e-7), None)
        if snap is not None:
            geometry[position] = [snap.longitude, snap.latitude]
    return geometry if b >= a else geometry[::-1]


def _leg(path: NumericPath, segments: RouteSegments, snaps: tuple[Snap, Snap], hills: bool,
         deadline: float | None) -> LegResponse:
    coordinates: list[list[float]] = []
    traversals: list[TraversalResponse] = []
    for number, a, b in zip(path.segment_ids, path.start_m, path.end_m):
        check_deadline(deadline)
        segment = segments.records[int(number)]
        geometry = _coordinates(segment, segments.projected[int(number)], float(a), float(b), snaps)
        for point in geometry:
            if not coordinates or point != coordinates[-1]:
                coordinates.append(point)
        traversals.append(TraversalResponse(segment_id=segment["id"], start_m=float(a), end_m=float(b)))
    sums = path.components.sum(axis=0).tolist()
    assert path.cost is not None
    breakdown = CostBreakdown(distance_m=sums[0], centre=sums[1], side=sums[2], reuse=sums[3],
                              hills=sums[4], total=path.cost)
    observations = (hill_observations(HillFeatures(*path.hill_features.sum(axis=0).tolist()))
                    if hills else PreferenceObservations())
    return LegResponse(coordinates=coordinates, distance_m=sums[0], cost=path.cost,
                       cost_breakdown=breakdown, observations=observations,
                       expanded_nodes=path.expanded_nodes, traversals=traversals)


def _repeated(candidate: NumericRunCandidate) -> float:
    spans: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for path in candidate.legs:
        for number, a, b in zip(path.segment_ids, path.start_m, path.end_m):
            low, high = sorted((float(a), float(b)))
            spans[int(number)].extend(((low, 1), (high, -1)))
    repeated = 0.0
    for events in spans.values():
        count, previous = 0, 0.0
        for position, delta in sorted(events):
            repeated += (position - previous) * max(0, count - 1)
            count += delta
            previous = position
    return repeated


def render_numeric_result(response: RunResponse, result: NumericRunResult, segments: RouteSegments,
                          started: float, *, hills: bool, deadline: float | None = None) -> RunResponse:
    assert response.home is not None and response.turnaround is not None
    snaps = response.home, response.turnaround
    for candidate in result.candidates:
        check_deadline(deadline)
        legs = [_leg(p, segments, snaps, hills, deadline) for p in candidate.legs]
        response.candidates.append(candidate_response(response, legs, candidate.outbound_left, _repeated(candidate)))
    response.status = "deadline" if "deadline" in result.outcomes else ("complete" if result.candidates else "unreachable")
    response.outcomes = list(result.outcomes)
    response.elapsed_s = monotonic()-started
    return response

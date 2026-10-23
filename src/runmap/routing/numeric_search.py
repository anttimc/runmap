"""Native routing primitives over immutable numeric chunks."""

from dataclasses import dataclass
import math
import sqlite3
from time import monotonic, perf_counter

import numpy as np
from numpy.typing import NDArray

from runmap import _routing_native
from runmap.paths.database import NetworkMetadata, network_metadata
from runmap.paths.numeric_database import iter_numeric_payloads
from runmap.routing.cost_preparation import resolve_shape_strengths
from runmap.routing.deadlines import check_deadline
from runmap.routing.run_types import RunSettings
from runmap.routing.profiles import exploration_profile
from runmap.routing.types import Bounds, EdgeProfile


Point = tuple[float, float]
ShapeArguments = tuple[Point, Point, float, float, float, float, float, float, bool]


@dataclass(frozen=True)
class LoadedNumericGraph:
    native: _routing_native.NumericGraph
    metadata: NetworkMetadata
    bounds: Bounds
    chunk_count: int
    buffer_bytes: int
    payload_load_ms: float
    index_prepare_ms: float


@dataclass(frozen=True)
class NumericEndpoint:
    node_id: int | None = None
    segment_id: int | None = None
    graph_offset_m: float = 0.0
    chainage_m: float | None = None
    point: Point | None = None
    hill_intervals: bytes | None = None


@dataclass(frozen=True)
class NumericShapeCosts:
    home: Point
    turnaround: Point
    settings: RunSettings
    penalize_right: bool
    used_segment_ids: tuple[int, ...] = ()
    used_spans: tuple[tuple[int, float, float], ...] = ()


@dataclass(frozen=True)
class NumericDistances:
    node_ids: NDArray[np.int64]
    values: NDArray[np.float64]
    frontier_node_ids: NDArray[np.int64]
    expanded_nodes: int
    start_node_id: int


@dataclass(frozen=True)
class NumericPath:
    cost: float | None
    expanded_nodes: int
    arc_ids: NDArray[np.int64]
    segment_ids: NDArray[np.int64]
    start_m: NDArray[np.float64]
    end_m: NDArray[np.float64]
    components: NDArray[np.float64]
    hill_features: NDArray[np.float64]
    frontier_node_ids: NDArray[np.int64]
    start_node_id: int
    destination_node_id: int


def _remaining(deadline: float | None) -> float | None:
    check_deadline(deadline)
    return None if deadline is None else max(0.0, deadline - monotonic())


def load_numeric_graph(connection: sqlite3.Connection, bounds: Bounds, *,
                       deadline: float | None = None, verify_checksums: bool = False) -> LoadedNumericGraph:
    metadata = network_metadata(connection)
    started = perf_counter()
    payloads: list[bytes] = []
    for stored in iter_numeric_payloads(connection, bounds, verify_checksums=verify_checksums):
        check_deadline(deadline)
        payloads.append(stored.payload)
    payload_ms = (perf_counter()-started)*1000
    started = perf_counter()
    native = _routing_native.NumericGraph(payloads, bounds=bounds, distance_scale=metadata.distance_scale,
                                          timeout_seconds=_remaining(deadline))
    index_ms = (perf_counter()-started)*1000
    return LoadedNumericGraph(native, metadata, bounds, len(payloads), sum(map(len, payloads)), payload_ms, index_ms)


def _segment_endpoint(endpoint: NumericEndpoint) -> tuple[int, float, float | None] | None:
    if endpoint.segment_id is None:
        return None
    return endpoint.segment_id, endpoint.graph_offset_m, endpoint.chainage_m


def _profile_index(graph: LoadedNumericGraph, profile: EdgeProfile | None) -> int:
    profile = profile or exploration_profile(avoid_stairs=False)
    try:
        return graph.metadata.models.profiles.index(profile)
    except ValueError as error:
        raise ValueError("Native routing requires a persisted routing profile") from error


def numeric_distances(graph: LoadedNumericGraph, start: NumericEndpoint, maximum_m: float = math.inf, *,
                      profile: EdgeProfile | None = None, reverse: bool = False,
                      deadline: float | None = None) -> NumericDistances:
    nodes, values, frontier, expanded, start_id = graph.native.distances(
        start.node_id, start_segment=_segment_endpoint(start), profile=_profile_index(graph, profile),
        maximum=maximum_m, reverse=reverse, timeout_seconds=_remaining(deadline))
    return NumericDistances(np.frombuffer(nodes, dtype="<i8"), np.frombuffer(values, dtype="<f8"),
                            np.frombuffer(frontier, dtype="<i8"), expanded, start_id)


def _shape_arguments(shape: NumericShapeCosts | None) -> ShapeArguments | None:
    if shape is None:
        return None
    strengths = resolve_shape_strengths(shape.settings)
    separation = math.dist(shape.home, shape.turnaround)
    if not all(math.isfinite(v) for v in (*shape.home, *shape.turnaround)) or separation < 0.01:
        raise ValueError("Home and turnaround must be spatially distinct")
    if shape.settings.avoidance_ratio == 0:
        return None
    settings = shape.settings
    spacing = min(5.0, separation * min(settings.radius_ratio, settings.transition_ratio) / 4)
    return (shape.home, shape.turnaround, separation * settings.radius_ratio,
            separation * settings.transition_ratio, spacing,
            strengths.centre, strengths.side, strengths.reuse, shape.penalize_right)


def numeric_shortest_path(graph: LoadedNumericGraph, start: NumericEndpoint, destination: NumericEndpoint, *,
                          profile: EdgeProfile | None = None, hill_weight: float = 0.0,
                          shape: NumericShapeCosts | None = None, deadline: float | None = None) -> NumericPath:
    if hill_weight > 0 and graph.metadata.models.hill_model is None:
        raise ValueError("Hill features are unavailable")
    intervals = {endpoint.segment_id: endpoint.hill_intervals for endpoint in (start, destination)
                 if hill_weight > 0 and endpoint.segment_id is not None and endpoint.hill_intervals is not None}
    result = graph.native.shortest_path(start.node_id, destination.node_id,
        start_segment=_segment_endpoint(start), destination_segment=_segment_endpoint(destination),
        start_point=start.point, destination_point=destination.point,
        profile=_profile_index(graph, profile), hill_weight=hill_weight, shape=_shape_arguments(shape),
        used_segments=() if shape is None else shape.used_segment_ids,
        used_spans=() if shape is None else shape.used_spans, hill_intervals=list(intervals.items()),
        timeout_seconds=_remaining(deadline))
    cost, expanded, arcs, segments, starts, ends, components, hills, frontier, start_id, destination_id = result
    return NumericPath(cost, expanded, np.frombuffer(arcs, dtype="<i8"), np.frombuffer(segments, dtype="<i8"),
                       np.frombuffer(starts, dtype="<f8"), np.frombuffer(ends, dtype="<f8"),
                       np.frombuffer(components, dtype="<f8").reshape(-1, 5),
                       np.frombuffer(hills, dtype="<f8").reshape(-1, 7),
                       np.frombuffer(frontier, dtype="<i8"), start_id, destination_id)

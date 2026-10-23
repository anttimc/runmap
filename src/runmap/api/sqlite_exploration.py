"""Bounded forward searches retain the graph ordering owned by each snapshot."""

from dataclasses import dataclass, fields, is_dataclass
import math
import sqlite3
from sys import getsizeof
from time import monotonic

import numpy as np

from runmap.api.sqlite_data import SQLitePathData
from runmap.paths.database import SELECTION, query_segment_record
from runmap.paths.exploration import DistanceData, Snap, distance_data
from runmap.paths.local_network import load_local_network
from runmap.routing.deadlines import check_deadline
from runmap.routing.numeric_exploration import NumericDistanceData
from runmap.routing.numeric_search import numeric_distances
from runmap.routing.geometry import projected_points
from runmap.routing.profiles import exploration_profile
from runmap.routing.search import GraphData
from runmap.routing.types import Bounds, EdgeProfile


@dataclass(frozen=True)
class ExplorationLimits:
    max_area_km2: float = 2500
    max_segments: int = 50000
    max_retries: int = 2


class ExplorationAreaError(RuntimeError):
    def __init__(self, reason: str, bounds: Bounds, retries: int) -> None:
        super().__init__("Exploration area limit reached; try a smaller distance budget")
        self.reason = reason
        self.bounds = bounds
        self.retries = retries


@dataclass(frozen=True)
class LocalExploration:
    routing: GraphData
    distances: DistanceData
    bounds: Bounds
    retries: int


@dataclass(frozen=True)
class NumericExploration:
    distances: NumericDistanceData
    bounds: Bounds
    retries: int
    nodes: int
    segments: int
    chunks: int
    buffer_bytes: int


def prepare_local_exploration(data: SQLitePathData, snap: Snap, budget_km: float,
                               surface: str, avoid_stairs: bool, deadline: float,
                               limits: ExplorationLimits) -> LocalExploration | NumericExploration:
    if not math.isfinite(budget_km) or not 0 < budget_km <= 50:
        raise ValueError("Budget must be greater than zero and at most 50 km")
    check_deadline(deadline)
    profile = exploration_profile(surface, avoid_stairs)
    with data.pool.connection(deadline=deadline) as connection:
        check_deadline(deadline)
        segment = query_segment_record(connection, snap.segment_id)
        bounds = exploration_bounds(projected_points(segment["geometry"]), budget_km * 1000,
                                     data.metadata.distance_scale)
        if data.numeric is None:
            return _search_windows(connection, snap, budget_km * 1000, profile, bounds, deadline, limits)
        return _search_numeric_windows(data, connection, snap, budget_km * 1000, profile, bounds, deadline, limits)


def exploration_bounds(points: list[tuple[float, float]], budget_m: float, distance_scale: float) -> Bounds:
    """Enclose full start geometry plus all budget-reachable endpoint positions."""
    if not math.isfinite(distance_scale) or not 0 < distance_scale <= 1:
        raise ValueError("Invalid prepared distance scale")
    # Starting with the entire snapped segment protects partial starts even on
    # long/bent segments. Whole subsequent arcs obey the global endpoint bound.
    radius = budget_m / distance_scale + 1
    xs, ys = zip(*points)
    return min(xs) - radius, min(ys) - radius, max(xs) + radius, max(ys) + radius


def _search_windows(connection: sqlite3.Connection, snap: Snap, maximum_m: float,
                     profile: EdgeProfile, bounds: Bounds, deadline: float,
                     limits: ExplorationLimits) -> LocalExploration:
    for retry in range(limits.max_retries + 1):
        check_deadline(deadline)
        _check_window(connection, bounds, retry, limits)
        local = load_local_network(connection, bounds, profile, include_terrain=False, include_coverage=False)
        check_deadline(deadline)
        distances = distance_data(local.routing, snap, maximum_m, deadline)
        # Conservative: even a policy-blocked unloaded link triggers expansion.
        reached = any(distances.outward.values[node] <= maximum_m + 1e-8 for node in local.frontier_indices)
        if not reached:
            return LocalExploration(local.routing, distances, bounds, retry)
        if retry == limits.max_retries:
            raise ExplorationAreaError("unloaded_connections", bounds, retry)
        west, south, east, north = bounds
        dx, dy = (east - west) / 2, (north - south) / 2
        bounds = west - dx, south - dy, east + dx, north + dy
        del local, distances
    raise AssertionError("Exploration retry loop did not run")


def _search_numeric_windows(data: SQLitePathData, connection: sqlite3.Connection, snap: Snap, maximum_m: float,
                            profile: EdgeProfile, bounds: Bounds, deadline: float,
                            limits: ExplorationLimits) -> NumericExploration:
    assert data.numeric is not None
    for retry in range(limits.max_retries + 1):
        check_deadline(deadline)
        _check_window(connection, bounds, retry, limits)
        graph, start = data.numeric.prepare_start(bounds, snap, deadline)
        outward = numeric_distances(graph, start, maximum_m, profile=profile, deadline=deadline)
        if not len(outward.frontier_node_ids):
            return NumericExploration(NumericDistanceData(snap, maximum_m, outward), bounds, retry,
                graph.native.node_count, graph.native.segment_count, graph.chunk_count, graph.buffer_bytes)
        if retry == limits.max_retries:
            raise ExplorationAreaError("unloaded_connections", bounds, retry)
        west, south, east, north = bounds
        dx, dy = (east-west)/2, (north-south)/2
        bounds = west-dx, south-dy, east+dx, north+dy
        del graph, outward
    raise AssertionError("Exploration retry loop did not run")


def _check_window(connection: sqlite3.Connection, bounds: Bounds, retry: int,
                   limits: ExplorationLimits) -> None:
    west, south, east, north = bounds
    if (east - west) * (north - south) > limits.max_area_km2 * 1e6:
        raise ExplorationAreaError("maximum_area", bounds, retry)
    count = connection.execute(f"SELECT count(*) FROM ({SELECTION} LIMIT :limit)",
                               dict(west=west, south=south, east=east, north=north,
                                    limit=limits.max_segments + 1)).fetchone()[0]
    if count > limits.max_segments:
        raise ExplorationAreaError("maximum_segments", bounds, retry)


def retained_exploration_bytes(value: object, seen: set[int] | None = None) -> int:
    """Charge shared graph/buffer owners once without materializing metadata views."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = getsizeof(value)
    if isinstance(value, memoryview):
        return size + retained_exploration_bytes(value.obj, seen)
    if isinstance(value, np.ndarray):
        return size + (retained_exploration_bytes(value.base, seen) if value.base is not None else 0)
    if isinstance(value, dict):
        return size + sum(retained_exploration_bytes(key, seen) + retained_exploration_bytes(item, seen)
                          for key, item in value.items())
    if isinstance(value, (tuple, list, set, frozenset)):
        return size + sum(retained_exploration_bytes(item, seen) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return size + getsizeof(getattr(value, "__dict__", {})) + sum(
            retained_exploration_bytes(getattr(value, field.name), seen) for field in fields(value))
    return size

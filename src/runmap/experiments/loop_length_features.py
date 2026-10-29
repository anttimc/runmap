"""Compute label-independent directed network features for saved loop-grid samples."""

from contextlib import closing
from dataclasses import dataclass, replace
import math
from pathlib import Path
import sqlite3
from time import monotonic, perf_counter

from runmap.api.sqlite_data import SQLitePathData
from runmap.experiments.loop_length_grid import GridSample
from runmap.paths.database import query_segment_endpoints
from runmap.paths.exploration import Snap
from runmap.paths.preparation import PreparedPolicy
from runmap.routing.geometry import projected_points
from runmap.routing.numeric_exploration import NumericDistanceData, numeric_distance_from_start
from runmap.routing.types import EdgeProfile
from runmap.routing.numeric_search import numeric_distances


@dataclass(frozen=True)
class NetworkFeature:
    forward_index: int
    lateral_index: int
    radius_m: float
    outward_m: float
    returning_m: float
    forward_m: float
    lateral_m: float


def distance_to_snap(connection: sqlite3.Connection, distances: NumericDistanceData, snap: Snap,
                     policy_id: str, *, reverse: bool = False) -> float:
    endpoints = query_segment_endpoints(connection, snap.segment_id)
    row = connection.execute("SELECT forward, backward, reason FROM segment_policies WHERE segment_id=? AND policy_id=?",
                             (snap.segment_id, policy_id)).fetchone()
    if row is None:
        raise ValueError("Missing prepared segment policy")
    policy = PreparedPolicy(policy_id, bool(row[0]), bool(row[1]), row[2])
    if reverse:
        policy = replace(policy, forward=policy.backward, backward=policy.forward)
    value = numeric_distance_from_start(distances, snap.segment_id, endpoints, policy, snap.graph_offset_m)
    if value is None:
        raise ValueError(f"No directed baseline distance to {snap.segment_id}")
    return value


def collect_positions(samples: list[GridSample]) -> tuple[Snap, dict[tuple[int, int], Snap], float]:
    positions: dict[tuple[int, int], Snap] = {}
    home: Snap | None = None
    maximum = 0.0
    for sample in samples:
        response = sample.response
        if response is None or response.status != "complete":
            continue
        assert response.home is not None and response.turnaround is not None
        if home is not None and home != response.home:
            raise ValueError("Grid samples must share the same snapped start")
        home = response.home
        key = sample.point.forward_index, sample.point.lateral_index
        if key in positions and positions[key] != response.turnaround:
            raise ValueError("Turnaround snapping changes across widths")
        positions[key] = response.turnaround
        maximum = max(maximum, *(leg.distance_m for candidate in response.candidates for leg in candidate.legs))
    if home is None:
        raise ValueError("No complete loops to analyze")
    return home, positions, math.ceil(maximum) + 100.0


def network_features(database: Path, samples: list[GridSample], dataset_id: str,
                     profile: EdgeProfile, bearing_deg: float = 0.0) -> tuple[list[NetworkFeature], dict[str, object]]:
    home, positions, maximum = collect_positions(samples)
    started = perf_counter()
    with closing(SQLitePathData(database)) as data:
        if data.numeric is None or data.dataset_id != dataset_id:
            raise ValueError("Network features require the original grid's matching numeric dataset")
        hx, hy = projected_points([[home.longitude, home.latitude]])[0]
        # The persisted A* distance scale is a lower bound in projected coordinates.
        radius = maximum / data.metadata.distance_scale + 100.0
        bounds = hx - radius, hy - radius, hx + radius, hy + radius
        graph, endpoint = data.numeric.prepare_start(bounds, home, monotonic() + 120)
        prepared_s = perf_counter() - started
        forward = numeric_distances(graph, endpoint, maximum, profile=profile, deadline=monotonic() + 120)
        forward_s = perf_counter() - started - prepared_s
        reverse = numeric_distances(graph, endpoint, maximum, profile=profile, reverse=True, deadline=monotonic() + 120)
        reverse_s = perf_counter() - started - prepared_s - forward_s
        if len(forward.frontier_node_ids) or len(reverse.frontier_node_ids):
            raise ValueError("Network distance search reached the loaded boundary; enlarge the feature window")
        features: list[NetworkFeature] = []
        angle = math.radians(bearing_deg)
        with data.pool.connection() as connection:
            for (i, j), snap in sorted(positions.items()):
                x, y = projected_points([[snap.longitude, snap.latitude]])[0]
                outward = distance_to_snap(connection, NumericDistanceData(home, maximum, forward), snap, profile["id"])
                returning = distance_to_snap(connection, NumericDistanceData(home, maximum, reverse), snap,
                                             profile["id"], reverse=True)
                dx, dy = x - hx, y - hy
                features.append(NetworkFeature(i, j, math.hypot(dx, dy), outward, returning,
                    dx * math.sin(angle) + dy * math.cos(angle), -dx * math.cos(angle) + dy * math.sin(angle)))
        metadata: dict[str, object] = {"distance_budget_m": maximum, "bounds_epsg3067": bounds,
            "distance_scale": data.metadata.distance_scale, "prepare_s": prepared_s,
            "forward_dijkstra_s": forward_s, "reverse_dijkstra_s": reverse_s,
            "total_feature_s": perf_counter() - started, "positions": len(features),
            "nodes": graph.native.node_count, "buffer_bytes": graph.buffer_bytes,
            "profile": profile, "source": "Two unpenalized directed Dijkstra searches; no loop-length labels used as features"}
        metadata["budget_selection"] = "Maximum observed feasible leg plus 100 m sets search extent only; feature values are independent shortest distances"
    return features, metadata

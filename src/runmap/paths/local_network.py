"""Pack request-sized SQLite windows without reclassifying cropped topology."""

from dataclasses import dataclass, replace
import sqlite3

import numpy as np

from runmap.paths.database import (NetworkMetadata, network_metadata, query_network,
                                   query_segment_features)
from runmap.paths.coverage import near_coverage_boundary
from runmap.paths.dead_ends import Classification
from runmap.paths.measurements import MeasurementData, pack_measurement_data
from runmap.paths.preparation import PreparedSegment
from runmap.routing.hill_types import HillData, HillFeatures
from runmap.routing.profiles import exploration_profile
from runmap.routing.search import GraphData, PreparedGraphPolicy, build_graph_index
from runmap.routing.types import Bounds, EdgeProfile, MeasurementDocument


@dataclass(frozen=True)
class LocalNetwork:
    metadata: NetworkMetadata
    bounds: Bounds
    routing: GraphData
    measurements: MeasurementData | None
    hills: HillData | None
    building_access: dict[str, Classification]
    frontier_nodes: frozenset[str]
    frontier_indices: frozenset[int]
    coverage_limited_segments: frozenset[str]


def load_local_network(connection: sqlite3.Connection, bounds: Bounds,
                       profile: EdgeProfile | None = None, *, include_terrain: bool = True,
                       include_measurements: bool = True) -> LocalNetwork:
    """Retain complete segments and distinguish unloaded links from source edges."""
    metadata = network_metadata(connection)
    profile = profile or exploration_profile(avoid_stairs=False)
    if profile not in metadata.models.profiles:
        raise ValueError("Local network requires a persisted routing profile")
    window = query_network(connection, bounds, include_measurements=include_terrain and include_measurements)
    features = query_segment_features(connection, bounds, include_hills=include_terrain)
    if features.keys() != {item["id"] for item in window.graph["segments"]}:
        raise ValueError("Missing prepared segment features")
    policy = _graph_policy(features, profile["id"])
    routing = build_graph_index(window.graph, profile, prepared_policy=policy)
    # Keep the dataset-wide lower bound used by routing heuristics. A cropped
    # graph must not accidentally increase it by omitting a constraining arc.
    routing = replace(routing, projected=replace(routing.projected, distance_scale=metadata.distance_scale))
    index = routing.integer
    if index is None:
        raise ValueError("Local network has no integer graph")
    measurements = None
    if include_terrain and include_measurements and metadata.method is not None:
        document = MeasurementDocument(schema_version=1, graph_digest=metadata.dataset_id,
                                       method=metadata.method, segments=window.measurements)
        measurements = pack_measurement_data(routing.document, document, index=index)
    hills = _pack_hills(routing, metadata, features) if include_terrain else None
    classifications = {key: item.building_access for key, item in features.items()
                       if item.building_access is not None}
    frontier = frozenset(window.frontier_nodes)
    limited = frozenset(key for key, segment in routing.segments.items()
                        if metadata.coverage is None or near_coverage_boundary(metadata.coverage, segment["geometry"]))
    return LocalNetwork(metadata, bounds, routing, measurements, hills, classifications,
                        frontier, frozenset(index.node_indices[node] for node in frontier), limited)


def _graph_policy(features: dict[str, PreparedSegment], profile_id: str) -> PreparedGraphPolicy:
    directions: dict[str, tuple[bool, bool]] = {}
    reasons: dict[str, str] = {}
    for key, item in features.items():
        policy = next((policy for policy in item.policies if policy.policy_id == profile_id), None)
        if policy is None:
            raise ValueError("Missing prepared segment policy")
        directions[key] = policy.forward, policy.backward
        if policy.reason is not None and policy.reason != "prepared_route_boundary":
            reasons[key] = policy.reason
    return PreparedGraphPolicy({key for key, item in features.items() if item.boundary_blocked},
                                reasons, directions)


def _hill_values(item: HillFeatures) -> tuple[float, ...]:
    return (item.penalty_m, item.ascent_m, item.descent_m, item.valid_length_m,
            item.trusted_length_m, item.fallback_length_m, item.length_m)


def _pack_hills(routing: GraphData, metadata: NetworkMetadata,
                features: dict[str, PreparedSegment]) -> HillData | None:
    model, index = metadata.models.hill_model, routing.integer
    if model is None:
        return None
    if index is None:
        raise ValueError("Local hills require an integer graph")
    offsets = np.zeros(len(index.segment_ids) + 1, dtype=np.int64)
    for number, key in enumerate(index.segment_ids):
        offsets[number + 1] = offsets[number] + len(features[key].hill_intervals)
    count = int(offsets[-1])
    chainage = np.empty((count, 2), dtype=np.float64)
    values = np.empty((count, 2, 7), dtype=np.float64)
    totals = np.empty((len(index.segment_ids), 2, 7), dtype=np.float64)
    for number, key in enumerate(index.segment_ids):
        item = features[key]
        if not item.hill_intervals or item.forward_hills is None or item.backward_hills is None:
            raise ValueError("Missing prepared hill features")
        for position, interval in enumerate(item.hill_intervals, int(offsets[number])):
            chainage[position] = interval.graph_start_m, interval.graph_end_m
            values[position] = _hill_values(interval.forward), _hill_values(interval.backward)
        totals[number] = _hill_values(item.forward_hills), _hill_values(item.backward_hills)
    length = float(totals[:, 0, 6].sum())
    valid = float(totals[:, 0, 3].sum())
    trusted = float(totals[:, 0, 4].sum())
    for array in (offsets, chainage, values, totals):
        array.flags.writeable = False
    return HillData(routing.document, metadata.dataset_id, model, index, offsets, chainage, values,
                    totals, valid / length if length else 0.0, trusted / length if length else 0.0)

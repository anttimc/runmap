"""Convert bounded offline records into numeric routing columns."""

from collections.abc import Sequence
import sqlite3

import numpy as np

from runmap.paths.database_features import read_segment_features
from runmap.paths.graph_chunks import TopologyChunk
from runmap.paths.numeric_format import ChunkCounts, NumericArray, NumericChunk
from runmap.paths.preparation import PreparedSegment
from runmap.routing.hill_types import HillFeatures
from runmap.routing.profiles import arc_forward
from runmap.routing.types import EdgeProfile


def _prepared_segments(connection: sqlite3.Connection, chunk: TopologyChunk) -> dict[str, PreparedSegment]:
    numbers = {f"s{i}": item["number"] for i, item in enumerate(chunk["segments"])}
    placeholders = ",".join(":" + key for key in numbers)
    prefix = f"WITH selected AS (SELECT * FROM segments WHERE number IN ({placeholders})) "
    return read_segment_features(connection, prefix, numbers)


def _node_arrays(connection: sqlite3.Connection, chunk: TopologyChunk) -> dict[str, NumericArray]:
    records = [item["record"] for item in chunk["nodes"]]
    keys = [item["id"] for item in records]
    placeholders = ",".join("?" for _ in keys)
    rows = {row[0]: (row[1], row[2], row[3]) for row in connection.execute(f"""
        SELECT n.id, n.rowid, p.x, p.y FROM nodes n
        JOIN projected_nodes p ON p.node_id = n.id WHERE n.id IN ({placeholders})
    """, keys)}
    if set(rows) != set(keys):
        raise ValueError("Missing numeric chunk node identity or projected coordinates")
    return {
        "node_ids": np.array([rows[key][0] for key in keys], dtype="<i8"),
        "node_xy": np.array([rows[key][1:] for key in keys], dtype="<f8").reshape(-1, 2),
        "node_degree": np.array([item["degree"] for item in chunk["nodes"]], dtype="<i8"),
    }


def _segment_arrays(chunk: TopologyChunk, features: dict[str, PreparedSegment],
                    nodes: dict[str, int]) -> dict[str, NumericArray]:
    records = [item["record"] for item in chunk["segments"]]
    geometries = [features[item["id"]].projected_geometry for item in records]
    offsets = np.zeros(len(records) + 1, dtype="<i8")
    offsets[1:] = np.cumsum([len(points) for points in geometries])
    return {
        "segment_ids": np.array([item["number"] for item in chunk["segments"]], dtype="<i8"),
        "segment_nodes": np.array([(nodes[s["from"]], nodes[s["to"]]) for s in records], dtype="<i8").reshape(-1, 2),
        "segment_bounds": np.array([item["bounds"] for item in chunk["segments"]], dtype="<f8").reshape(-1, 4),
        "geometry_offsets": offsets,
        "geometry_xy": np.array([point for points in geometries for point in points], dtype="<f8").reshape(-1, 2),
    }


def _arc_arrays(chunk: TopologyChunk, features: dict[str, PreparedSegment], nodes: dict[str, int],
                profiles: tuple[EdgeProfile, ...]) -> dict[str, NumericArray]:
    segments = {item["record"]["id"]: (number, item["record"])
                for number, item in enumerate(chunk["segments"])}
    records = [item["record"] for item in chunk["arcs"]]
    reverse = [not arc_forward(segments[arc["segment_id"]][1], arc) for arc in records]
    policies = {key: {p.policy_id: p for p in feature.policies} for key, feature in features.items()}
    allowed = np.empty((len(records), len(profiles)), dtype=np.uint8)
    for i, (arc, backward) in enumerate(zip(records, reverse)):
        for j, profile in enumerate(profiles):
            policy = policies[arc["segment_id"]][profile["id"]]
            allowed[i, j] = policy.backward if backward else policy.forward
    return {
        "arc_ids": np.array([item["number"] for item in chunk["arcs"]], dtype="<i8"),
        "arc_nodes": np.array([(nodes[a["from"]], nodes[a["to"]]) for a in records], dtype="<i8").reshape(-1, 2),
        "arc_segments": np.array([segments[a["segment_id"]][0] for a in records], dtype="<i8"),
        "arc_lengths": np.array([a["length_m"] for a in records], dtype="<f8"),
        "arc_reverse": np.array(reverse, dtype=np.uint8),
        "arc_allowed": allowed,
    }


def _adjacency_arrays(endpoints: NumericArray, count: int) -> dict[str, NumericArray]:
    arrays: dict[str, NumericArray] = {}
    for direction, column in (("outgoing", 0), ("incoming", 1)):
        nodes = endpoints[:, column]
        offsets = np.zeros(count + 1, dtype="<i8")
        offsets[1:] = np.cumsum(np.bincount(nodes, minlength=count))
        arrays[direction + "_offsets"] = offsets
        arrays[direction + "_arcs"] = np.argsort(nodes, kind="stable").astype("<i8")
    return arrays


def _hill_values(features: HillFeatures) -> tuple[float, ...]:
    return (features.penalty_m, features.ascent_m, features.descent_m, features.valid_length_m,
            features.trusted_length_m, features.fallback_length_m, features.length_m)


def _hill_arrays(features: Sequence[PreparedSegment]) -> dict[str, NumericArray]:
    offsets = np.zeros(len(features) + 1, dtype="<i8")
    offsets[1:] = np.cumsum([len(item.hill_intervals) for item in features])
    count = int(offsets[-1])
    chainage = np.empty((count, 2), dtype="<f8")
    values = np.empty((count, 2, 7), dtype="<f8")
    totals = np.zeros((len(features), 2, 7), dtype="<f8")
    for i, item in enumerate(features):
        if item.forward_hills is not None and item.backward_hills is not None:
            totals[i] = _hill_values(item.forward_hills), _hill_values(item.backward_hills)
        for position, interval in enumerate(item.hill_intervals, int(offsets[i])):
            chainage[position] = interval.graph_start_m, interval.graph_end_m
            values[position] = _hill_values(interval.forward), _hill_values(interval.backward)
    return {"hill_offsets": offsets, "hill_chainage": chainage,
            "hill_features": values, "hill_totals": totals}


def prepare_numeric_chunk(connection: sqlite3.Connection, chunk: TopologyChunk,
                          profiles: tuple[EdgeProfile, ...]) -> NumericChunk:
    """Bound record preparation to one offline chunk, never the complete dataset."""
    features = _prepared_segments(connection, chunk)
    nodes = {item["record"]["id"]: i for i, item in enumerate(chunk["nodes"])}
    if set(features) != {item["record"]["id"] for item in chunk["segments"]}:
        raise ValueError("Missing prepared numeric chunk features")
    arrays = _node_arrays(connection, chunk)
    arrays.update(_segment_arrays(chunk, features, nodes))
    arrays.update(_arc_arrays(chunk, features, nodes, profiles))
    arrays.update(_adjacency_arrays(arrays["arc_nodes"], len(nodes)))
    arrays.update(_hill_arrays([features[item["record"]["id"]] for item in chunk["segments"]]))
    counts = ChunkCounts(len(nodes), len(chunk["segments"]), len(chunk["arcs"]), len(profiles),
                         len(arrays["geometry_xy"]), len(arrays["hill_chainage"]))
    return NumericChunk(counts, arrays)

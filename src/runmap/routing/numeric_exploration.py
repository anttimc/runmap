"""Resolve inspected intervals from sparse distances without retaining a graph."""

from dataclasses import dataclass
import math

import numpy as np

from runmap.paths.exploration import Snap
from runmap.paths.preparation import PreparedEndpoints, PreparedPolicy
from runmap.routing.numeric_search import NumericDistances


@dataclass(frozen=True)
class NumericDistanceData:
    snap: Snap
    maximum_m: float
    outward: NumericDistances


@dataclass(frozen=True)
class NumericSegmentDistances:
    anchors: tuple[tuple[float, float], ...]
    policy: PreparedPolicy
    maximum_m: float


def numeric_segment_distances(distances: NumericDistanceData, segment_id: str, endpoints: PreparedEndpoints,
                              policy: PreparedPolicy) -> NumericSegmentDistances:
    if policy.reason is not None:
        return NumericSegmentDistances((), policy, distances.maximum_m)
    anchors = [(0.0, _node_distance(distances.outward, endpoints.source)),
               (endpoints.length_m, _node_distance(distances.outward, endpoints.target))]
    if segment_id == distances.snap.segment_id:
        anchors.append((distances.snap.graph_offset_m, 0.0))
    return NumericSegmentDistances(tuple(anchors), policy, distances.maximum_m)


def _node_distance(distances: NumericDistances, node: int) -> float:
    nodes = distances.node_ids
    # Native results list sorted original IDs followed by any virtual start.
    if len(nodes) and nodes[-1] < 0:
        nodes = nodes[:-1]
    index = int(np.searchsorted(nodes, node))
    if index == len(nodes) or nodes[index] != node:
        return math.inf
    return float(distances.values[index])


def _point_distance(distances: NumericSegmentDistances, offset: float) -> float:
    choices: list[float] = []
    policy = distances.policy
    for position, base in distances.anchors:
        delta = offset - position
        if (policy.forward and delta >= -1e-8) or (policy.backward and delta <= 1e-8):
            choices.append(base + abs(delta))
    return min(choices, default=math.inf)


def numeric_interval_inclusion(distances: NumericDistanceData, segment_id: str, endpoints: PreparedEndpoints,
                               policy: PreparedPolicy, start_m: float, end_m: float,
                               budget_m: float, *, prepared: NumericSegmentDistances | None = None) -> dict[str, object]:
    if policy.reason is not None:
        return {"inclusion_state": "excluded", "inclusion_reason": policy.reason,
                "minimum_inclusion_m": None, "directions": []}
    prepared = prepared or numeric_segment_distances(distances, segment_id, endpoints, policy)
    directions: list[dict[str, str | float | None]] = []
    finite: list[float] = []
    for forward, allowed in ((True, policy.forward), (False, policy.backward)):
        if not allowed:
            continue
        start = start_m if forward else end_m
        outward = _point_distance(prepared, start)
        total = outward + abs(end_m-start_m) if outward <= distances.maximum_m else math.inf
        if math.isfinite(total):
            finite.append(total)
        directions.append({"direction": "forward" if forward else "backward",
                           "minimum_inclusion_m": total if math.isfinite(total) else None})
    minimum = min(finite, default=None)
    included = minimum is not None and minimum <= budget_m + 1e-8
    return {"inclusion_state": "included" if included else "beyond_budget",
            "inclusion_reason": "entire_interval_within_budget" if included else "beyond_distance_budget",
            "minimum_inclusion_m": minimum, "distance_lower_bound_m": budget_m if minimum is None else None,
            "directions": directions}


def numeric_distance_from_start(distances: NumericDistanceData, segment_id: str, endpoints: PreparedEndpoints,
                                policy: PreparedPolicy, offset_m: float, *,
                                prepared: NumericSegmentDistances | None = None) -> float | None:
    if policy.reason is not None:
        return None
    prepared = prepared or numeric_segment_distances(distances, segment_id, endpoints, policy)
    value = _point_distance(prepared, offset_m)
    return value if math.isfinite(value) and value <= distances.maximum_m else None

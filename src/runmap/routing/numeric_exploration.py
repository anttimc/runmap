"""Resolve display intervals from sparse native distances without retaining a graph."""

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


def _node_distance(distances: NumericDistances, node: int) -> float:
    nodes = distances.node_ids
    # Native results list sorted original IDs followed by any virtual start.
    if len(nodes) and nodes[-1] < 0:
        nodes = nodes[:-1]
    index = int(np.searchsorted(nodes, node))
    if index == len(nodes) or nodes[index] != node:
        return math.inf
    return float(distances.values[index])


def _point_distance(distances: NumericDistanceData, segment_id: str, endpoints: PreparedEndpoints,
                    policy: PreparedPolicy, offset: float) -> float:
    choices: list[float] = []
    anchors = [(endpoints.source, 0.0), (endpoints.target, endpoints.length_m)]
    if segment_id == distances.snap.segment_id:
        anchors.append((-1, distances.snap.graph_offset_m))
    for node, position in anchors:
        delta = offset - position
        if (policy.forward and delta >= -1e-8) or (policy.backward and delta <= 1e-8):
            base = 0.0 if node == -1 else _node_distance(distances.outward, node)
            choices.append(base + abs(delta))
    return min(choices, default=math.inf)


def numeric_interval_inclusion(distances: NumericDistanceData, segment_id: str, endpoints: PreparedEndpoints,
                               policy: PreparedPolicy, start_m: float, end_m: float,
                               budget_m: float) -> dict[str, object]:
    if policy.reason is not None:
        return {"inclusion_state": "excluded", "inclusion_reason": policy.reason,
                "minimum_inclusion_m": None, "directions": []}
    directions: list[dict[str, str | float | None]] = []
    finite: list[float] = []
    for forward, allowed in ((True, policy.forward), (False, policy.backward)):
        if not allowed:
            continue
        start = start_m if forward else end_m
        outward = _point_distance(distances, segment_id, endpoints, policy, start)
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
                                policy: PreparedPolicy, offset_m: float) -> float | None:
    if policy.reason is not None:
        return None
    value = _point_distance(distances, segment_id, endpoints, policy, offset_m)
    return value if math.isfinite(value) and value <= distances.maximum_m else None

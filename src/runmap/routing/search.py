"""Shortest-path primitives and diverse outbound/return path pairs."""

from collections import Counter, defaultdict
from array import array
from collections.abc import Callable, Iterable, Mapping, Sequence, Set
from dataclasses import dataclass, field, replace
import heapq
import math
from time import monotonic
from typing import TypeVar

import numpy as np

from runmap.routing.arc_storage import ArcLookup, ArcRecords
from runmap.routing.types import Arc, Coordinate, EdgeProfile, GraphDocument, Node, RouteMetrics, Segment
from runmap.routing.geometry import ProjectedGeometry, prepare_projected_geometry
from runmap.routing.integer_graph import (AdjacencyLookup, IntegerArc, IntegerGraph, apply_integer_policy,
                                         external_arc, integer_arc, integer_arc_id, integer_node_id,
                                         integer_segment_id, prepare_integer_graph)

from runmap.routing.profiles import (
    arc_forward, classify_segments, exploration_profile, pedestrian_access_reason, pedestrian_directions,
)


NodeKey = TypeVar("NodeKey", str, int)


def _is_grade_separated(segment: Segment) -> bool:
    tags = segment.get("tags", {})
    bridge = str(tags.get("bridge", "")).lower()
    tunnel = str(tags.get("tunnel", "")).lower()
    layer = str(tags.get("layer", "0")).strip()
    return (
        bridge not in {"", "no", "false", "0"}
        or tunnel not in {"", "no", "false", "0"}
        or layer not in {"", "0"}
    )


def _orientation(first: Sequence[float], second: Sequence[float], third: Sequence[float]) -> float:
    return (
        (second[0] - first[0]) * (third[1] - first[1])
        - (second[1] - first[1]) * (third[0] - first[0])
    )


def _line_segments_intersect(
    first: Sequence[float], second: Sequence[float],
    third: Sequence[float], fourth: Sequence[float],
) -> bool:
    if (
        max(first[0], second[0]) < min(third[0], fourth[0])
        or max(third[0], fourth[0]) < min(first[0], second[0])
        or max(first[1], second[1]) < min(third[1], fourth[1])
        or max(third[1], fourth[1]) < min(first[1], second[1])
    ):
        return False
    one = _orientation(first, second, third)
    two = _orientation(first, second, fourth)
    three = _orientation(third, fourth, first)
    four = _orientation(third, fourth, second)
    epsilon = 1e-14
    if (
        ((one > epsilon and two < -epsilon) or (one < -epsilon and two > epsilon))
        and ((three > epsilon and four < -epsilon) or (three < -epsilon and four > epsilon))
    ):
        return True

    def on_segment(
        first_point: Sequence[float], second_point: Sequence[float], point: Sequence[float],
    ) -> bool:
        return (
            min(first_point[0], second_point[0]) - epsilon
            <= point[0]
            <= max(first_point[0], second_point[0]) + epsilon
            and min(first_point[1], second_point[1]) - epsilon
            <= point[1]
            <= max(first_point[1], second_point[1]) + epsilon
        )

    return (
        (abs(one) <= epsilon and on_segment(first, second, third))
        or (abs(two) <= epsilon and on_segment(first, second, fourth))
        or (abs(three) <= epsilon and on_segment(third, fourth, first))
        or (abs(four) <= epsilon and on_segment(third, fourth, second))
    )


def _boundary_blocked_segments(graph: GraphDocument) -> set[str]:
    bucket_size = 0.002
    boundary_buckets = defaultdict(list)
    boundary_edges = []
    endpoint_counts = Counter()
    for boundary in graph.get("route_boundaries", ()):
        endpoint_counts[tuple(boundary["geometry"][0])] += 1
        endpoint_counts[tuple(boundary["geometry"][-1])] += 1
        for first, second in zip(boundary["geometry"], boundary["geometry"][1:]):
            boundary_edges.append((first, second))
    query_bounds = graph.get("source", {}).get("osm", {}).get("bounds_wgs84")
    if query_bounds and len(query_bounds) == 4:
        south, west, north, east = query_bounds
        for endpoint, occurrences in endpoint_counts.items():
            if occurrences != 1:
                continue
            longitude, latitude = endpoint
            distances = (
                (abs(longitude - west), [west, latitude]),
                (abs(longitude - east), [east, latitude]),
                (abs(latitude - south), [longitude, south]),
                (abs(latitude - north), [longitude, north]),
            )
            distance, projected = min(distances, key=lambda item: item[0])
            if distance <= 0.003:
                boundary_edges.append((list(endpoint), projected))
    for first, second in boundary_edges:
        first_bucket = math.floor(min(first[1], second[1]) / bucket_size)
        last_bucket = math.floor(max(first[1], second[1]) / bucket_size)
        for bucket in range(first_bucket, last_bucket + 1):
            boundary_buckets[bucket].append((first, second))
    if not boundary_buckets:
        return set()
    blocked = set()
    for segment in graph["segments"]:
        if _is_grade_separated(segment):
            continue
        geometry = segment["geometry"]
        for first, second in zip(geometry, geometry[1:]):
            first_bucket = math.floor(min(first[1], second[1]) / bucket_size)
            last_bucket = math.floor(max(first[1], second[1]) / bucket_size)
            boundary_edges = {
                (tuple(edge[0]), tuple(edge[1]))
                for bucket in range(first_bucket, last_bucket + 1)
                for edge in boundary_buckets.get(bucket, ())
            }
            if any(
                _line_segments_intersect(first, second, edge[0], edge[1])
                for edge in boundary_edges
            ):
                blocked.add(segment["id"])
                break
    return blocked


@dataclass(frozen=True)
class CostModel:
    ascent_cost_per_m: float = 0.0
    stairs_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.ascent_cost_per_m < 0 or self.stairs_multiplier < 1:
            raise ValueError("Route cost parameters must be non-negative")


@dataclass(frozen=True)
class PathResult:
    arc_ids: tuple[str, ...]
    cost: float
    expanded_nodes: int = field(default=0, compare=False)


@dataclass(frozen=True)
class NodeDistances:
    """Immutable float64 node values and int64 discovery order, bound to shared IDs."""

    node_ids: tuple[str, ...]
    extra_node_ids: tuple[str, ...]
    values: memoryview
    reached: memoryview


def freeze_node_distances(index: IntegerGraph, values: array, reached: array) -> NodeDistances:
    """Seal numeric search buffers before sharing them between caches and snapshots."""
    if (values.typecode != "d" or reached.typecode != "q" or values.itemsize != 8 or reached.itemsize != 8
            or len(values) != len(index.node_ids) + len(index.extra_node_ids)):
        raise ValueError("Distance buffers must match the graph and use float64/int64 elements")
    return NodeDistances(index.node_ids, index.extra_node_ids,
                          memoryview(values.tobytes()).cast("d"), memoryview(reached.tobytes()).cast("q"))


def external_node_distances(distances: NodeDistances) -> dict[str, float]:
    """Translate only reached nodes for legacy callers requiring external IDs."""
    count = len(distances.node_ids)
    return {(distances.node_ids[node] if node < count else distances.extra_node_ids[node - count]):
            float(distances.values[node]) for node in distances.reached}


@dataclass(frozen=True)
class GraphData:
    document: GraphDocument
    nodes: Mapping[str, Node]
    segments: Mapping[str, Segment]
    arcs: Mapping[str, Arc]
    outgoing: Mapping[str, list[str]]
    incoming: Mapping[str, list[str]]
    degree: Mapping[str, int]
    blocked_nodes: set[str]
    boundary_blocked_segments: set[str]
    ineligible_segments: dict[str, str]
    profile: EdgeProfile
    projected: ProjectedGeometry
    direction_cache: Mapping[str, frozenset[bool]] = field(default_factory=dict)
    integer: IntegerGraph | None = None


def build_graph_index(graph: GraphDocument, profile: EdgeProfile | None = None) -> GraphData:
    profile = profile or exploration_profile(avoid_stairs=False)
    nodes = {node["id"]: node for node in graph["nodes"]}
    segments = {segment["id"]: segment for segment in graph["segments"]}
    arcs = {arc["id"]: arc for arc in graph["arcs"]}
    degree = Counter()
    for segment in graph["segments"]:
        degree[segment["from"]] += 1
        degree[segment["to"]] += 1
    integer = prepare_integer_graph(nodes, segments, arcs)
    index = GraphData(graph, nodes, segments, arcs, {}, {}, degree,
                      {node_id for node_id, node in nodes.items() if pedestrian_access_reason(node, profile)},
                      _boundary_blocked_segments(graph), {}, profile,
                      prepare_projected_geometry(nodes, segments, arcs.values(), integer), integer=integer)
    graph["arcs"] = ArcRecords(integer.arcs)
    index = replace(index, arcs=ArcLookup(integer.arcs, integer.arc_indices))
    index.ineligible_segments.update(classify_segments(index, profile))
    return _prepare_traversals(index)


def _prepare_traversals(index: GraphData) -> GraphData:
    """Resolve policy and direction tags before any search traverses the graph."""
    base = index.integer
    if base is None or base.extra_arcs or base.extra_node_ids or base.arc_overrides:
        raise ValueError("Policy preparation requires the base integer graph")
    allowed = np.zeros((len(base.segment_ids), 2), dtype=np.bool_)
    for number, segment_id in enumerate(base.segment_ids):
        segment = index.segments[segment_id]
        if segment_id in index.ineligible_segments or segment_id in index.boundary_blocked_segments:
            continue
        forward, backward = pedestrian_directions(segment.get("tags", {}), index.profile)
        allowed[number] = (forward and segment.get("forward", True), backward and segment.get("backward", True))
    sources, targets = np.asarray(base.arcs.sources), np.asarray(base.arcs.targets)
    segments = np.asarray(base.arcs.segments)
    reverse = np.asarray(base.arcs.reverse)
    blocked = np.zeros(len(base.node_ids), dtype=np.bool_)
    for node in index.blocked_nodes:
        blocked[base.node_indices[node]] = True
    enabled = (allowed[segments, reverse.astype(np.int8)]
               & ~blocked[sources] & ~blocked[targets])
    directions = np.zeros(len(base.segment_ids), dtype=np.uint8)
    np.bitwise_or.at(directions, segments[enabled], np.where(reverse[enabled], 2, 1).astype(np.uint8))
    integer = apply_integer_policy(base, enabled)
    direction_sets = (frozenset(), frozenset({True}), frozenset({False}), frozenset({True, False}))
    return replace(index, integer=integer, outgoing=AdjacencyLookup(integer), incoming=AdjacencyLookup(integer, True),
                   direction_cache={segment: direction_sets[directions[i]] for i, segment in enumerate(base.segment_ids)})


def arc_usable(index: GraphData, arc: Arc, blocked_segments: Set[str] = frozenset()) -> bool:
    return (arc["segment_id"] not in index.boundary_blocked_segments
            and arc["segment_id"] not in index.ineligible_segments
            and arc["segment_id"] not in blocked_segments
            and arc["from"] not in index.blocked_nodes and arc["to"] not in index.blocked_nodes
            and arc_forward(index.segments[arc["segment_id"]], arc)
            in index.direction_cache[arc["segment_id"]])


def _arc_cost(
    index: GraphData, arc: Arc, model: CostModel,
    overlap_segments: Set[str], overlap_factor: float,
) -> float:
    segment = index.segments[arc["segment_id"]]
    cost = float(arc["length_m"])
    if segment["tags"].get("highway") == "steps":
        cost *= model.stairs_multiplier
    cost += float(arc.get("ascent_m", 0.0)) * model.ascent_cost_per_m
    if arc["segment_id"] in overlap_segments:
        cost += float(arc["length_m"]) * (overlap_factor - 1.0)
    return cost


def shortest_path(
    index: GraphData,
    start: str,
    destination: str,
    model: CostModel | None = None,
    overlap_segments: Set[str] = frozenset(),
    overlap_factor: float = 1.0,
    blocked_segments: Set[str] = frozenset(),
    *,
    heuristic: Callable[[str, str], float] | None = None,
    arc_cost: Callable[[Arc], float] | None = None,
    indexed_arc_cost: Callable[[IntegerArc], float] | None = None,
    indexed_heuristic: Callable[[int, int], float] | None = None,
    deadline: float | None = None,
) -> PathResult | None:
    """Find a least-cost path; an optional lower-bound heuristic enables A*.

    The heuristic takes node and destination IDs and must be deterministic,
    finite, non-negative, zero at the destination, and never overestimate the
    remaining cost under the selected cost model and overlap penalty.
    Indexed callbacks receive prepared arcs and integer node IDs. Use only one
    callback from each pair; returned paths keep their external arc IDs.
    """
    if arc_cost is not None and indexed_arc_cost is not None:
        raise ValueError("Choose one arc-cost callback")
    if heuristic is not None and indexed_heuristic is not None:
        raise ValueError("Choose one heuristic callback")
    if start not in index.nodes or destination not in index.nodes:
        raise ValueError("Route endpoint is not in the graph")
    if overlap_factor < 1:
        raise ValueError("Overlap factor must be at least one")
    if start in index.blocked_nodes or destination in index.blocked_nodes:
        return None
    model = model or CostModel()
    dense = index.integer
    if dense is None:
        raise ValueError("Graph has no prepared integer index")
    start_index, destination_index = dense.node_indices[start], dense.node_indices[destination]
    estimate = indexed_heuristic
    if heuristic is not None:
        def estimate(node: int, target: int) -> float:
            return heuristic(integer_node_id(dense, node), integer_node_id(dense, target))
    blocked = {dense.segment_indices[s] for s in blocked_segments if s in dense.segment_indices}
    overlap = {dense.segment_indices[s] for s in overlap_segments if s in dense.segment_indices}
    node_count = len(dense.node_ids) + len(dense.extra_node_ids)
    distances = [math.inf] * node_count
    distances[start_index] = 0.0
    predecessors = [-1] * node_count
    if _remaining_cost(estimate, destination_index, destination_index) != 0:
        raise ValueError("Heuristic must be zero at the destination")
    # Keep external IDs only as heap tie keys to preserve existing equal-cost paths.
    queue = [(_remaining_cost(estimate, start_index, destination_index), 0.0, start, start_index)]
    expanded = 0
    arc_count, enabled, arc_overrides = len(dense.arc_ids), dense.enabled, dense.arc_overrides
    offsets, entries = dense.outgoing.offsets, dense.outgoing.arcs
    while queue:
        if deadline is not None and monotonic() >= deadline:
            raise TimeoutError("Route search exceeded its deadline")
        _, distance, _, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        if node == destination_index:
            break
        expanded += 1
        outgoing = dense.outgoing_overrides.get(node)
        if outgoing is None:
            outgoing = entries[offsets[node]:offsets[node + 1]]
        for number in outgoing:
            if number < arc_count and number not in arc_overrides and not enabled[number]:
                continue
            prepared = integer_arc(dense, number)
            if prepared.segment in blocked:
                continue
            if indexed_arc_cost is not None:
                cost = indexed_arc_cost(prepared)
            elif arc_cost is not None:
                cost = arc_cost(external_arc(dense, number))
            else:
                cost = prepared.length_m
                if model.stairs_multiplier != 1 and index.segments[integer_segment_id(dense, prepared.segment)]["tags"].get("highway") == "steps":
                    cost *= model.stairs_multiplier
                if model.ascent_cost_per_m:
                    ascent = (dense.arcs.optional[number, 0] if number < arc_count and number not in arc_overrides
                              else float(external_arc(dense, number).get("ascent_m", 0.0)))
                    cost += ascent * model.ascent_cost_per_m
                if prepared.segment in overlap:
                    cost += prepared.length_m * (overlap_factor - 1.0)
            if not math.isfinite(cost) or cost < 0:
                raise ValueError("Arc cost must be finite and non-negative")
            candidate = distance + cost
            target = prepared.target
            if candidate < distances[target]:
                distances[target] = candidate
                predecessors[target] = prepared.index
                target_id = integer_node_id(dense, target)
                priority = candidate + _remaining_cost(estimate, target, destination_index)
                heapq.heappush(queue, (priority, candidate, target_id, target))
    if distances[destination_index] == math.inf:
        return None
    return PathResult(_integer_path(dense, predecessors, start_index, destination_index),
                      distances[destination_index], expanded)


def _integer_path(graph: IntegerGraph, predecessors: list[int], start: int,
                  destination: int) -> tuple[str, ...]:
    arc_ids: list[str] = []
    node = destination
    while node != start:
        arc = integer_arc(graph, predecessors[node])
        arc_ids.append(integer_arc_id(graph, arc.index))
        node = arc.source
    return tuple(reversed(arc_ids))


def _remaining_cost(
    heuristic: Callable[[NodeKey, NodeKey], float] | None, node_id: NodeKey, destination: NodeKey,
) -> float:
    if heuristic is None:
        return 0.0
    estimate = heuristic(node_id, destination)
    if not math.isfinite(estimate) or estimate < 0:
        raise ValueError("Heuristic must return finite non-negative costs")
    return estimate


def shortest_distances(
    index: GraphData, start: str, maximum: float = math.inf,
    blocked_segments: Set[str] = frozenset(), reverse: bool = False,
    deadline: float | None = None,
) -> dict[str, float]:
    """Adapt external IDs around the shared integer distance search."""
    if start not in index.nodes:
        raise ValueError("Route start is not in the graph")
    dense = index.integer
    if dense is None:
        raise ValueError("Graph has no prepared integer index")
    blocked = {dense.segment_indices[s] for s in blocked_segments if s in dense.segment_indices}
    result = indexed_shortest_distances(index, dense.node_indices[start], maximum, blocked, reverse, deadline)
    return external_node_distances(result)


def indexed_shortest_distances(
    index: GraphData, start: int, maximum: float = math.inf,
    blocked_segments: Set[int] = frozenset(), reverse: bool = False,
    deadline: float | None = None,
) -> NodeDistances:
    """Traverse prepared integer adjacency and retain immutable float64 distances."""
    dense = index.integer
    if dense is None:
        raise ValueError("Graph has no prepared integer index")
    count = len(dense.node_ids) + len(dense.extra_node_ids)
    if not 0 <= start < count:
        raise ValueError("Route start is not in the graph")
    check_time = monotonic
    if deadline is not None and check_time() > deadline:
        raise TimeoutError("Path preparation exceeded its deadline; try a smaller budget")
    distances = array("d", [math.inf]) * count
    reached = array("q")
    start_id = integer_node_id(dense, start)
    if start_id in index.blocked_nodes:
        return freeze_node_distances(dense, distances, reached)
    distances[start] = 0.0
    reached.append(start)
    queue = [(0.0, start_id, start)]
    adjacency = dense.incoming if reverse else dense.outgoing
    overrides = dense.incoming_overrides if reverse else dense.outgoing_overrides
    offsets, entries = adjacency.offsets, adjacency.arcs
    arc_count, enabled = len(dense.arc_ids), dense.enabled
    neighbours = dense.arcs.sources if reverse else dense.arcs.targets
    segments, lengths = dense.arcs.segments, dense.arcs.lengths
    arc_overrides = dense.arc_overrides
    while queue:
        if deadline is not None and check_time() > deadline:
            raise TimeoutError("Path preparation exceeded its deadline; try a smaller budget")
        distance, _, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        if distance > maximum:
            continue
        arcs = overrides.get(node)
        if arcs is None:
            arcs = entries[offsets[node]:offsets[node + 1]]
        for number in arcs:
            if number < arc_count and number not in arc_overrides:
                if not enabled[number] or (blocked_segments and segments[number] in blocked_segments):
                    continue
                neighbour = neighbours[number]
                length = lengths[number]
            else:
                arc = integer_arc(dense, number)
                if arc.segment in blocked_segments:
                    continue
                neighbour = arc.source if reverse else arc.target
                length = arc.length_m
            candidate = distance + length
            previous = distances[neighbour]
            if candidate <= maximum and candidate < previous:
                if math.isinf(previous):
                    reached.append(neighbour)
                distances[neighbour] = candidate
                heapq.heappush(queue, (candidate, integer_node_id(dense, neighbour), neighbour))
    return freeze_node_distances(dense, distances, reached)


def diverse_path_pair(
    index: GraphData, start: str, destination: str, model: CostModel, overlap_factor: float,
) -> tuple[PathResult, PathResult] | None:
    """Find an outbound path and a return path penalizing outbound segments."""
    outbound = shortest_path(index, start, destination, model)
    if outbound is None:
        return None
    used = frozenset(index.arcs[arc_id]["segment_id"] for arc_id in outbound.arc_ids)
    returning = shortest_path(
        index,
        destination,
        start,
        model,
        overlap_segments=used,
        overlap_factor=overlap_factor,
    )
    if returning is None:
        return None
    return outbound, returning


def route_metrics(index: GraphData, arc_ids: Iterable[str]) -> RouteMetrics:
    """Summarize a closed-walk candidate without hiding repeated traversal."""
    arcs = [index.arcs[arc_id] for arc_id in arc_ids]
    counts = Counter(arc["segment_id"] for arc in arcs)
    total_length = sum(float(arc["length_m"]) for arc in arcs)
    repeated = sum(
        float(index.segments[segment_id]["length_m"]) * count
        for segment_id, count in counts.items()
        if count > 1
    )
    stair_distance = sum(
        float(arc["length_m"])
        for arc in arcs
        if index.segments[arc["segment_id"]]["tags"].get("highway") == "steps"
    )
    reversals = sum(
        first["segment_id"] == second["segment_id"]
        for first, second in zip(arcs, arcs[1:])
    )
    start_access_reversal = bool(
        len(arcs) > 1 and arcs[0]["segment_id"] == arcs[-1]["segment_id"]
    )
    return {
        "distance_m": round(total_length, 3),
        "ascent_m": round(sum(float(arc.get("ascent_m", 0.0)) for arc in arcs), 3),
        "descent_m": round(sum(float(arc.get("descent_m", 0.0)) for arc in arcs), 3),
        "repeated_distance_m": round(repeated, 3),
        "repeated_fraction": round(repeated / total_length if total_length else 0.0, 6),
        "stair_distance_m": round(stair_distance, 3),
        "immediate_reversals": reversals,
        "start_access_reversal": start_access_reversal,
        "start_access_repeated_distance_m": round(
            2 * float(arcs[0]["length_m"]) if start_access_reversal else 0.0, 3
        ),
        "arc_count": len(arcs),
        "distinct_segment_count": len(counts),
    }


def route_coordinates(index: GraphData, arc_ids: Iterable[str]) -> list[Coordinate]:
    coordinates = []
    for arc_id in arc_ids:
        arc = index.arcs[arc_id]
        segment = index.segments[arc["segment_id"]]
        geometry = segment["geometry"]
        if arc["from"] != segment["from"]:
            geometry = reversed(geometry)
        for coordinate in geometry:
            if not coordinates or coordinate != coordinates[-1]:
                coordinates.append(list(coordinate))
    return coordinates


def apply_profile(index: GraphData, profile: EdgeProfile) -> GraphData:
    """Reuse topology while applying the same policy to nodes and physical segments."""
    result = replace(index, profile=profile, direction_cache={}, ineligible_segments={}, blocked_nodes={
        node_id for node_id, node in index.nodes.items() if pedestrian_access_reason(node, profile)
    })
    result.ineligible_segments.update(classify_segments(result, profile))
    return _prepare_traversals(result)

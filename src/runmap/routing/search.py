"""Shortest-path primitives and diverse outbound/return path pairs."""

from collections import Counter, defaultdict
from dataclasses import dataclass
import heapq
import math


_DENIED = frozenset({"no", "private"})
_ALLOWED = frozenset({"yes", "designated", "permissive", "official"})


def _is_grade_separated(segment):
    tags = segment.get("tags", {})
    bridge = str(tags.get("bridge", "")).lower()
    tunnel = str(tags.get("tunnel", "")).lower()
    layer = str(tags.get("layer", "0")).strip()
    return (
        bridge not in {"", "no", "false", "0"}
        or tunnel not in {"", "no", "false", "0"}
        or layer not in {"", "0"}
    )


def _orientation(first, second, third):
    return (
        (second[0] - first[0]) * (third[1] - first[1])
        - (second[1] - first[1]) * (third[0] - first[0])
    )


def _line_segments_intersect(first, second, third, fourth):
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

    def on_segment(first_point, second_point, point):
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


def _boundary_blocked_segments(graph):
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

    def __post_init__(self):
        if self.ascent_cost_per_m < 0 or self.stairs_multiplier < 1:
            raise ValueError("Route cost parameters must be non-negative")


@dataclass(frozen=True)
class PathResult:
    arc_ids: tuple
    cost: float


class GraphIndex:
    """Memory index over the portable prepared-graph document."""

    def __init__(self, graph):
        self.document = graph
        self.nodes = {node["id"]: node for node in graph["nodes"]}
        self.segments = {segment["id"]: segment for segment in graph["segments"]}
        self.arcs = {arc["id"]: arc for arc in graph["arcs"]}
        self.outgoing = defaultdict(list)
        self.degree = Counter()
        for arc in graph["arcs"]:
            self.outgoing[arc["from"]].append(arc["id"])
        for segment in graph["segments"]:
            self.degree[segment["from"]] += 1
            self.degree[segment["to"]] += 1
        self.blocked_nodes = {
            node["id"] for node in graph["nodes"]
            if node.get("barrier")
            and str(node.get("access", "")).lower() in _DENIED
            and str(node.get("foot", "")).lower() not in _ALLOWED
        }
        self.boundary_blocked_segments = _boundary_blocked_segments(graph)


def _arc_cost(index, arc, model, overlap_segments, overlap_factor):
    segment = index.segments[arc["segment_id"]]
    cost = float(arc["length_m"])
    if segment["tags"].get("highway") == "steps":
        cost *= model.stairs_multiplier
    cost += float(arc.get("ascent_m", 0.0)) * model.ascent_cost_per_m
    if arc["segment_id"] in overlap_segments:
        cost += float(arc["length_m"]) * (overlap_factor - 1.0)
    return cost


def shortest_path(
    index,
    start,
    destination,
    model=None,
    overlap_segments=frozenset(),
    overlap_factor=1.0,
):
    """Find one least-cost directed path with optional segment reuse penalty."""
    if start not in index.nodes or destination not in index.nodes:
        raise ValueError("Route endpoint is not in the graph")
    if overlap_factor < 1:
        raise ValueError("Overlap factor must be at least one")
    model = model or CostModel()
    distances = {start: 0.0}
    predecessors = {}
    queue = [(0.0, start)]
    while queue:
        distance, node_id = heapq.heappop(queue)
        if distance != distances.get(node_id):
            continue
        if node_id == destination:
            break
        for arc_id in index.outgoing.get(node_id, ()):
            arc = index.arcs[arc_id]
            if arc["segment_id"] in index.boundary_blocked_segments:
                continue
            if arc["to"] in index.blocked_nodes and arc["to"] != destination:
                continue
            candidate = distance + _arc_cost(
                index, arc, model, overlap_segments, overlap_factor
            )
            if candidate < distances.get(arc["to"], math.inf):
                distances[arc["to"]] = candidate
                predecessors[arc["to"]] = arc_id
                heapq.heappush(queue, (candidate, arc["to"]))
    if destination not in distances:
        return None
    arc_ids = []
    node_id = destination
    while node_id != start:
        arc_id = predecessors[node_id]
        arc_ids.append(arc_id)
        node_id = index.arcs[arc_id]["from"]
    arc_ids.reverse()
    return PathResult(tuple(arc_ids), distances[destination])


def shortest_distances(index, start, maximum=math.inf):
    """Calculate physical-length distance from a start to reachable vertices."""
    if start not in index.nodes:
        raise ValueError("Route start is not in the graph")
    distances = {start: 0.0}
    queue = [(0.0, start)]
    while queue:
        distance, node_id = heapq.heappop(queue)
        if distance != distances.get(node_id):
            continue
        if distance > maximum:
            continue
        for arc_id in index.outgoing.get(node_id, ()):
            arc = index.arcs[arc_id]
            if arc["segment_id"] in index.boundary_blocked_segments:
                continue
            if arc["to"] in index.blocked_nodes:
                continue
            candidate = distance + float(arc["length_m"])
            if candidate <= maximum and candidate < distances.get(arc["to"], math.inf):
                distances[arc["to"]] = candidate
                heapq.heappush(queue, (candidate, arc["to"]))
    return distances


def diverse_path_pair(index, start, destination, model, overlap_factor):
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


def route_metrics(index, arc_ids):
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


def route_coordinates(index, arc_ids):
    coordinates = []
    for arc_id in arc_ids:
        arc = index.arcs[arc_id]
        segment = index.segments[arc["segment_id"]]
        geometry = segment["geometry"]
        if arc["from"] != segment["from"]:
            geometry = reversed(geometry)
        for coordinate in geometry:
            if not coordinates or coordinate != coordinates[-1]:
                coordinates.append(coordinate)
    return coordinates

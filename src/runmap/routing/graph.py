"""Packed graph preparation and pedestrian policy shared with offline exports."""

import math, numpy as np
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field, replace

from runmap.routing.arc_storage import ArcLookup, ArcRecords
from runmap.routing.geometry import ProjectedGeometry, prepare_projected_geometry
from runmap.routing.integer_graph import (
    AdjacencyLookup,
    IntegerGraph,
    apply_integer_policy,
    prepare_integer_graph,
)
from runmap.routing.node_storage import MetadataLookup, NodeRecords, pack_nodes
from runmap.routing.profiles import (
    arc_forward,
    classify_segments,
    exploration_profile,
    pedestrian_access_reason,
    pedestrian_directions,
)
from runmap.routing.segment_storage import SegmentRecords, pack_segments
from runmap.routing.types import Arc, EdgeProfile, GraphDocument, Node, Segment


BOUNDARY_VERSION = "prepared-route-boundaries-v1"


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


@dataclass(frozen=True)
class PreparedGraphPolicy:
    boundary_blocked_segments: set[str]
    ineligible_segments: dict[str, str]
    directions: Mapping[str, tuple[bool, bool]]


def build_graph_index(graph: GraphDocument, profile: EdgeProfile | None = None,
                      *, prepared_policy: PreparedGraphPolicy | None = None) -> GraphData:
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
                      (_boundary_blocked_segments(graph) if prepared_policy is None
                       else prepared_policy.boundary_blocked_segments), {}, profile,
                      prepare_projected_geometry(nodes, segments, arcs.values(), integer), integer=integer)
    graph["arcs"] = ArcRecords(integer.arcs)
    graph["nodes"] = NodeRecords(pack_nodes(nodes, integer.node_ids))
    graph["segments"] = SegmentRecords(pack_segments(
        segments, integer.segment_ids, integer.node_ids, integer.node_indices, index.projected.wgs84))
    index = replace(index, arcs=ArcLookup(integer.arcs, integer.arc_indices),
                    nodes=MetadataLookup(graph["nodes"], integer.node_indices),
                    segments=MetadataLookup(graph["segments"], integer.segment_indices))
    index.ineligible_segments.update(classify_segments(index, profile) if prepared_policy is None
                                     else prepared_policy.ineligible_segments)
    return _prepare_traversals(index, None if prepared_policy is None else prepared_policy.directions)


def _prepare_traversals(index: GraphData,
                        prepared_directions: Mapping[str, tuple[bool, bool]] | None = None) -> GraphData:
    """Resolve policy and direction tags before any search traverses the graph."""
    base = index.integer
    if base is None or base.extra_arcs or base.extra_node_ids or base.arc_overrides:
        raise ValueError("Policy preparation requires the base integer graph")
    allowed = np.zeros((len(base.segment_ids), 2), dtype=np.bool_)
    for number, segment_id in enumerate(base.segment_ids):
        segment = index.segments[segment_id]
        if segment_id in index.ineligible_segments or segment_id in index.boundary_blocked_segments:
            continue
        if prepared_directions is None:
            forward, backward = pedestrian_directions(segment.get("tags", {}), index.profile)
            allowed[number] = (forward and segment.get("forward", True), backward and segment.get("backward", True))
        else:
            allowed[number] = prepared_directions[segment_id]
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


def apply_profile(index: GraphData, profile: EdgeProfile) -> GraphData:
    """Reuse topology while applying the same policy to nodes and physical segments."""
    result = replace(index, profile=profile, direction_cache={}, ineligible_segments={}, blocked_nodes={
        node_id for node_id, node in index.nodes.items() if pedestrian_access_reason(node, profile)
    })
    result.ineligible_segments.update(classify_segments(result, profile))
    return _prepare_traversals(result)

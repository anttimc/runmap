"""Directed one-way and round-trip runs over the prepared pedestrian network."""

from collections import ChainMap, Counter
from dataclasses import replace
from functools import partial
import math
from time import monotonic
from typing import Literal
from collections.abc import Callable

from runmap.paths.exploration import Snap, legal_directions, segment_reason
from runmap.routing.cost_preparation import (
    build_request_cost_data, effective_run_settings, penalty_strengths, resolve_traversal,
)
from runmap.routing.cost_types import LegCostContext, PreferenceSettings, RequestCostCache, RequestCostData
from runmap.routing.cost_settings import resolve_preferences
from runmap.routing.costs import indexed_arc_cost, path_cost_breakdown, path_observations
from runmap.routing.hill_types import HillData
from runmap.routing.deadlines import check_deadline
from runmap.routing.geometry import (prepare_projected_geometry, projected_points, slice_geometry,
                                     indexed_node_point, indexed_segment_points)
from runmap.routing.integer_graph import extend_integer_graph, integer_arc, integer_segment_id
from runmap.routing.run_types import (
    PhysicalPart, RunCandidate, RunLeg, RunMode, RunOverlay, RunResult, RunSettings,
)
from runmap.routing.search import GraphData, PathResult, arc_usable, shortest_path
from runmap.routing.types import Arc, Node, Segment


def endpoint_overlay(index: GraphData, home: Snap, turnaround: Snap,
                     deadline: float | None = None) -> RunOverlay:
    """Split each affected physical segment once, sharing coincident junctions."""
    base = index.integer
    if base is None:
        raise ValueError("Graph has no prepared integer index")
    nodes: dict[str, Node] = {}
    segments: dict[str, Segment] = {}
    arcs: dict[str, Arc] = {}
    parts: dict[str, PhysicalPart] = {}
    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, list[str]] = {}
    endpoints: dict[str, str] = {}
    for segment_id in sorted({home.segment_id, turnaround.segment_id}):
        check_deadline(deadline)
        if segment_reason(index, segment_id):
            raise ValueError("Run endpoint is on an ineligible segment")
        segment = index.segments[segment_id]
        geometry = indexed_segment_points(index.projected, base.segment_indices[segment_id])
        total = sum(math.dist(a, b) for a, b in zip(geometry, geometry[1:]))
        length = segment["length_m"]
        cuts = [(0.0, 0.0, segment["from"]), (length, total, segment["to"])]
        for label, snap in (("home", home), ("turnaround", turnaround)):
            if snap.segment_id != segment_id:
                continue
            if not (0 <= snap.graph_offset_m <= length and 0 <= snap.chainage_m <= total + 0.01):
                raise ValueError("Run endpoint offsets are outside the segment")
            matching = next((cut for cut in cuts if abs(cut[0] - snap.graph_offset_m) < 1e-7), None)
            node = matching[2] if matching else f"@run-{label}"
            endpoints[label] = node
            if not matching:
                nodes[node] = {"id": node, "longitude": snap.longitude, "latitude": snap.latitude}
                cuts.append((snap.graph_offset_m, min(total, snap.chainage_m), node))
        cuts.sort()
        for _, _, node in cuts:
            outgoing[node] = [a for a in index.outgoing.get(node, ())
                              if index.arcs[a]["segment_id"] not in {home.segment_id, turnaround.segment_id}]
            incoming[node] = [a for a in index.incoming.get(node, ())
                              if index.arcs[a]["segment_id"] not in {home.segment_id, turnaround.segment_id}]
        for number, (first, second) in enumerate(zip(cuts, cuts[1:])):
            part_id = f"@run:{segment_id}:{number}"
            part_geometry = slice_geometry(segment["geometry"], first[1], second[1])
            for position, node_id in ((0, first[2]), (-1, second[2])):
                node = nodes[node_id] if node_id in nodes else index.nodes[node_id]
                part_geometry[position] = [node["longitude"], node["latitude"]]
            part: Segment = {"id": part_id, "osm_way_id": segment.get("osm_way_id", ""),
                             "from": first[2], "to": second[2], "length_m": second[0] - first[0],
                             "geometry": part_geometry,
                             "tags": segment["tags"], "forward": segment.get("forward", True),
                             "backward": segment.get("backward", True)}
            segments[part_id] = part
            parts[part_id] = PhysicalPart(base.segment_indices[segment_id], first[0], second[0])
            for forward in sorted(legal_directions(index, segment_id), reverse=True):
                a, b = (first[2], second[2]) if forward else (second[2], first[2])
                arc_id = f"{part_id}:{'f' if forward else 'b'}"
                arcs[arc_id] = {"id": arc_id, "segment_id": part_id, "from": a, "to": b,
                                "length_m": part["length_m"]}
    # Populate after processing both segments so a shared junction keeps all new arcs.
    for arc in arcs.values():
        outgoing[arc["from"]].append(arc["id"])
        incoming[arc["to"]].append(arc["id"])
    all_segments = ChainMap(segments, index.segments)
    integer = extend_integer_graph(index.integer, nodes, segments, arcs, outgoing, incoming,
                                   segment_records=all_segments)
    graph = replace(index, nodes=ChainMap(nodes, index.nodes), segments=all_segments,
                    arcs=ChainMap(arcs, index.arcs), outgoing=ChainMap(outgoing, index.outgoing),
                    incoming=ChainMap(incoming, index.incoming), direction_cache=ChainMap({
                        part_id: index.direction_cache[integer_segment_id(base, part.segment)]
                        for part_id, part in parts.items()
                    }, index.direction_cache),
                    projected=prepare_projected_geometry(nodes, segments, arcs.values(), integer, index.projected),
                    integer=integer)
    if endpoints["home"] == endpoints["turnaround"]:
        raise ValueError("Home and turnaround must snap to distinct points")
    assert graph.integer is not None
    return RunOverlay(graph, endpoints["home"], endpoints["turnaround"], {
        graph.integer.segment_indices[part]: source for part, source in parts.items()})


def distance_heuristic(graph: GraphData, deadline: float | None) -> Callable[[str, str], float]:
    """Reuse the prepared lower bound, including any temporary endpoint arcs."""
    check_deadline(deadline)
    integer = graph.integer
    if integer is None:
        raise ValueError("Graph has no prepared integer index")
    geometry = graph.projected
    scale = graph.projected.distance_scale * (1 - 1e-12)

    def estimate(node: str, destination: str) -> float:
        return scale * math.dist(indexed_node_point(geometry, integer.node_indices[node]),
                                 indexed_node_point(geometry, integer.node_indices[destination]))

    return estimate


def indexed_distance_heuristic(graph: GraphData, deadline: float | None) -> Callable[[int, int], float]:
    """Use shared projected coordinates directly from integer node IDs."""
    check_deadline(deadline)
    geometry = graph.projected
    scale = geometry.distance_scale * (1 - 1e-12)

    def estimate(node: int, destination: int) -> float:
        return scale * math.dist(indexed_node_point(geometry, node), indexed_node_point(geometry, destination))

    return estimate


def _run_leg(path: PathResult, data: RequestCostData, cache: RequestCostCache,
             context: LegCostContext) -> RunLeg:
    return RunLeg(path, path_cost_breakdown(path.arc_ids, data, cache, context),
                  tuple(resolve_traversal(data, data.graph.arcs[a]) for a in path.arc_ids),
                  path_observations(path.arc_ids, data, cache))


def _run_candidate(legs: tuple[RunLeg, ...], graph: GraphData, left: bool | None) -> RunCandidate:
    counts = Counter(graph.arcs[a]["segment_id"] for leg in legs for a in leg.path.arc_ids)
    repeated = sum(graph.segments[s]["length_m"] * (count - 1) for s, count in counts.items())
    distance = sum(graph.arcs[a]["length_m"] for leg in legs for a in leg.path.arc_ids)
    return RunCandidate(left, legs, distance, repeated)


def plan_run(index: GraphData, home: Snap, turnaround: Snap, settings: RunSettings,
             deadline: float | None = None, *, mode: RunMode = "round_trip",
             preferences: PreferenceSettings = PreferenceSettings(),
             hills: HillData | None = None,
             frontier_nodes: frozenset[int] = frozenset()) -> RunResult:
    """Plan directed routes, retaining completed candidates on deadline expiry."""
    started = monotonic()
    settings = effective_run_settings(settings, mode)
    resolved = resolve_preferences(preferences, hills=hills)
    if resolved.weights.hills and hills is not None and hills.graph is not index.document:
        raise ValueError("Hill features belong to a different prepared graph")
    overlay = endpoint_overlay(index, home, turnaround, deadline)
    graph = overlay.graph
    heuristic = indexed_distance_heuristic(graph, deadline)
    data = build_request_cost_data(graph, home, turnaround, settings,
                                   parts=overlay.parts, preferences=preferences, hills=hills)
    cache = RequestCostCache()
    candidates: list[RunCandidate] = []
    reached_frontier: set[int] = set()

    def observe(node: int) -> None:
        if node in frontier_nodes:
            reached_frontier.add(node)

    def search_leg(start: str, end: str, context: LegCostContext) -> PathResult | None:
        return shortest_path(graph, start, end,
            indexed_arc_cost=partial(indexed_arc_cost, data=data, cache=cache, leg=context, deadline=deadline),
            indexed_heuristic=heuristic, deadline=deadline, visit_node=observe if frontier_nodes else None)

    outcomes: list[Literal["complete", "unreachable", "deadline"]] = []
    for left in ((True,) if data.shape_disabled else (True, False)):
        try:
            outbound_context = LegCostContext(left)
            outbound = search_leg(overlay.start, overlay.destination, outbound_context)
            if outbound is None:
                outcomes.append("unreachable")
                continue
            legs = (_run_leg(outbound, data, cache, outbound_context),)
            if mode == "round_trip":
                used = frozenset(integer_arc(data.index, data.index.arc_indices[a]).segment for a in outbound.arc_ids)
                return_context = LegCostContext(not left, used)
                returning = reverse_path(graph, outbound) if data.shape_disabled and data.hills is None else None
                if returning is None:
                    returning = search_leg(overlay.destination, overlay.start, return_context)
                if returning is None:
                    outcomes.append("unreachable")
                    continue
                legs += (_run_leg(returning, data, cache, return_context),)
            candidates.append(_run_candidate(legs, graph, left if mode == "round_trip" else None))
            outcomes.append("complete")
        except TimeoutError:
            outcomes.append("deadline")
    return RunResult(overlay, tuple(candidates), tuple(outcomes), monotonic() - started,
                     data.preferences, frozenset(reached_frontier))


def reverse_path(graph: GraphData, path: PathResult) -> PathResult | None:
    """Retrace exactly when pedestrian directions permit every reverse traversal."""
    reverse: list[str] = []
    for arc_id in reversed(path.arc_ids):
        arc = graph.arcs[arc_id]
        counterpart = next((candidate for candidate in graph.outgoing.get(arc["to"], ())
                            if graph.arcs[candidate]["to"] == arc["from"]
                            and graph.arcs[candidate]["segment_id"] == arc["segment_id"]
                            and arc_usable(graph, graph.arcs[candidate])), None)
        if counterpart is None:
            return None
        reverse.append(counterpart)
    return PathResult(tuple(reverse), sum(graph.arcs[a]["length_m"] for a in reverse))

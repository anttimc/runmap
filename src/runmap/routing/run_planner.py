"""Distance-based two-leg runs over the prepared pedestrian network."""

from collections import ChainMap, Counter
from dataclasses import replace
from functools import partial
import math
from time import monotonic
from typing import Literal
from collections.abc import Callable

from runmap.paths.exploration import Snap, legal_directions, segment_reason
from runmap.routing.cost_preparation import build_request_cost_data, penalty_strengths, validate_settings
from runmap.routing.cost_types import LegCostContext, RequestCostCache
from runmap.routing.costs import arc_cost
from runmap.routing.deadlines import check_deadline
from runmap.routing.geometry import projected_points, slice_geometry
from runmap.routing.run_types import (
    PhysicalPart, RunCandidate, RunOverlay, RunResult, RunSettings,
)
from runmap.routing.search import GraphData, PathResult, arc_usable, shortest_path
from runmap.routing.types import Arc, Node, Segment


def endpoint_overlay(index: GraphData, home: Snap, turnaround: Snap,
                     deadline: float | None = None) -> RunOverlay:
    """Split each affected physical segment once, sharing coincident junctions."""
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
        geometry = projected_points(segment["geometry"])
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
            parts[part_id] = PhysicalPart(segment_id, first[0], second[0])
            for forward in sorted(legal_directions(index, segment_id), reverse=True):
                a, b = (first[2], second[2]) if forward else (second[2], first[2])
                arc_id = f"{part_id}:{'f' if forward else 'b'}"
                arcs[arc_id] = {"id": arc_id, "segment_id": part_id, "from": a, "to": b,
                                "length_m": part["length_m"]}
    # Populate after processing both segments so a shared junction keeps all new arcs.
    for arc in arcs.values():
        outgoing[arc["from"]].append(arc["id"])
        incoming[arc["to"]].append(arc["id"])
    graph = replace(index, nodes=ChainMap(nodes, index.nodes), segments=ChainMap(segments, index.segments),
                    arcs=ChainMap(arcs, index.arcs), outgoing=ChainMap(outgoing, index.outgoing),
                    incoming=ChainMap(incoming, index.incoming), direction_cache={})
    if endpoints["home"] == endpoints["turnaround"]:
        raise ValueError("Home and turnaround must snap to distinct points")
    return RunOverlay(graph, endpoints["home"], endpoints["turnaround"], parts)


def distance_heuristic(graph: GraphData, deadline: float | None) -> Callable[[str, str], float]:
    """Calibrate a geometric lower bound against every directed base length."""
    identifiers = list(graph.nodes)
    points = projected_points([[graph.nodes[n]["longitude"], graph.nodes[n]["latitude"]] for n in identifiers])
    coordinates = dict(zip(identifiers, points))
    scale = 1.0
    for number, arc in enumerate(graph.arcs.values()):
        if number % 256 == 0:
            check_deadline(deadline)
        distance = math.dist(coordinates[arc["from"]], coordinates[arc["to"]])
        if distance:
            scale = min(scale, arc["length_m"] / distance)
    scale *= 1 - 1e-12

    def estimate(node: str, destination: str) -> float:
        return scale * math.dist(coordinates[node], coordinates[destination])

    return estimate


def plan_run(index: GraphData, home: Snap, turnaround: Snap, settings: RunSettings,
             deadline: float | None = None) -> RunResult:
    """Construct both side assignments, retaining completed runs on deadline expiry."""
    started = monotonic()
    validate_settings(settings)
    overlay = endpoint_overlay(index, home, turnaround, deadline)
    graph = overlay.graph
    heuristic = distance_heuristic(graph, deadline)
    cost_data = build_request_cost_data(graph, home, turnaround, settings)
    cost_cache = RequestCostCache()
    out_and_back = cost_data.distance_only

    candidates: list[RunCandidate] = []
    outcomes: list[Literal["complete", "unreachable", "deadline"]] = []
    for left in ((True,) if out_and_back else (True, False)):
        try:
            outbound = shortest_path(graph, overlay.home, overlay.turnaround,
                                     arc_cost=partial(arc_cost, data=cost_data, cache=cost_cache,
                                                      leg=LegCostContext(left), deadline=deadline),
                                     heuristic=heuristic, deadline=deadline)
            if outbound is None:
                outcomes.append("unreachable")
                continue
            used = frozenset(graph.arcs[a]["segment_id"] for a in outbound.arc_ids)
            returning = reverse_path(graph, outbound) if out_and_back else None
            if returning is None:
                returning = shortest_path(graph, overlay.turnaround, overlay.home,
                                      arc_cost=partial(arc_cost, data=cost_data, cache=cost_cache,
                                                       leg=LegCostContext(not left, used), deadline=deadline),
                                      heuristic=heuristic, deadline=deadline)
            if returning is None:
                outcomes.append("unreachable")
                continue
            counts = Counter(graph.arcs[a]["segment_id"] for a in outbound.arc_ids + returning.arc_ids)
            repeated = sum(graph.segments[s]["length_m"] * (count - 1) for s, count in counts.items())
            distance = sum(graph.arcs[a]["length_m"] for a in outbound.arc_ids + returning.arc_ids)
            candidates.append(RunCandidate(left, outbound, returning, distance, repeated))
            outcomes.append("complete")
        except TimeoutError:
            outcomes.append("deadline")
    return RunResult(overlay, tuple(candidates), tuple(outcomes), monotonic() - started)


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

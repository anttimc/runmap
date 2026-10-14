"""Distance-based two-leg runs over the prepared pedestrian network."""

from collections import ChainMap, Counter
from dataclasses import dataclass, replace
import math
from time import monotonic
from typing import Literal
from collections.abc import Callable

from rasterio.warp import transform

from runmap.paths.exploration import Snap, legal_directions, segment_reason
from runmap.routing.fields import Point, integrate_fields
from runmap.routing.search import GraphData, PathResult, arc_usable, shortest_path
from runmap.routing.types import Arc, Coordinate, Node, Segment


@dataclass(frozen=True)
class RunSettings:
    avoidance_ratio: float = 0.18
    centre_enabled: bool = True
    radius_ratio: float = 0.15
    transition_ratio: float = 0.05
    side_strength: float = 2.0
    reuse_strength: float = 4.0


@dataclass(frozen=True)
class PhysicalPart:
    segment_id: str
    start_m: float
    end_m: float


@dataclass(frozen=True)
class RunOverlay:
    graph: GraphData
    home: str
    turnaround: str
    parts: dict[str, PhysicalPart]


@dataclass(frozen=True)
class RunCandidate:
    outbound_left: bool
    outbound: PathResult
    returning: PathResult
    distance_m: float
    repeated_distance_m: float


@dataclass(frozen=True)
class RunResult:
    overlay: RunOverlay
    candidates: tuple[RunCandidate, ...]
    outcomes: tuple[Literal["complete", "unreachable", "deadline"], ...]
    elapsed_s: float


def check_deadline(deadline: float | None) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError("Run planning exceeded its deadline")


def projected_points(geometry: list[Coordinate]) -> list[Point]:
    xs, ys = transform("EPSG:4326", "EPSG:3067", *zip(*geometry))
    return list(zip(xs, ys))


def slice_geometry(geometry: list[Coordinate], start: float, end: float) -> list[Coordinate]:
    """Slice using projected chainage, retaining bends between the cut points."""
    points = projected_points(geometry)
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    result: list[Point] = []
    offset = 0.0
    for first, second, length in zip(points, points[1:], lengths):
        if length and offset + length >= start and offset <= end:
            for cut in (max(start, offset), min(end, offset + length)):
                fraction = (cut - offset) / length
                point = (first[0] + fraction * (second[0] - first[0]),
                         first[1] + fraction * (second[1] - first[1]))
                if not result or point != result[-1]:
                    result.append(point)
        offset += length
    if len(result) < 2:
        raise ValueError("Endpoint chainage does not span a valid path portion")
    xs, ys = transform("EPSG:3067", "EPSG:4326", *zip(*result))
    return [[x, y] for x, y in zip(xs, ys)]


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


def validate_settings(settings: RunSettings) -> None:
    values = (settings.avoidance_ratio, settings.radius_ratio, settings.transition_ratio,
              settings.side_strength, settings.reuse_strength)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Run settings must be finite")
    if not (0 <= settings.avoidance_ratio <= 1 and 0.02 <= settings.radius_ratio <= 1
            and 0.005 <= settings.transition_ratio <= 1
            and 0 <= settings.side_strength <= 100 and 0 <= settings.reuse_strength <= 100):
        raise ValueError("Run settings exceed supported ranges")
    if settings.avoidance_ratio / settings.radius_ratio > 5:
        raise ValueError("Avoidance distance must not exceed five Gaussian scales")


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


def penalty_strengths(settings: RunSettings) -> tuple[float, float, float]:
    """Ease all preferences in together, reaching full strength at 18 percent."""
    validate_settings(settings)
    fraction = min(1.0, settings.avoidance_ratio / 0.18)
    ramp = fraction * fraction * (3 - 2 * fraction)
    centre = ramp * math.exp((settings.avoidance_ratio / settings.radius_ratio) ** 2 / 2)
    return (centre if settings.centre_enabled else 0.0,
            ramp * settings.side_strength, ramp * settings.reuse_strength)


def plan_run(index: GraphData, home: Snap, turnaround: Snap, settings: RunSettings,
             deadline: float | None = None) -> RunResult:
    """Construct both side assignments, retaining completed runs on deadline expiry."""
    started = monotonic()
    validate_settings(settings)
    overlay = endpoint_overlay(index, home, turnaround, deadline)
    graph = overlay.graph
    heuristic = distance_heuristic(graph, deadline)
    home_point, destination = projected_points([[home.longitude, home.latitude],
                                                [turnaround.longitude, turnaround.latitude]])
    separation = math.dist(home_point, destination)
    if separation < 0.01:
        raise ValueError("Home and turnaround must be spatially distinct")
    out_and_back = settings.avoidance_ratio == 0
    strength, side_strength, reuse_strength = penalty_strengths(settings)
    fields: dict[str, tuple[float, float, float]] = {}

    def cost(arc: Arc, right: bool, used: frozenset[str]) -> float:
        check_deadline(deadline)
        if out_and_back:
            return arc["length_m"]
        segment_id = arc["segment_id"]
        if segment_id not in fields:
            points = projected_points(graph.segments[segment_id]["geometry"])
            fields[segment_id] = integrate_fields(points, home_point, destination,
                                                  separation * settings.radius_ratio,
                                                  separation * settings.transition_ratio,
                                                  min(5.0, separation * min(settings.radius_ratio, settings.transition_ratio) / 4),
                                                  deadline)
            check_deadline(deadline)
        radial, right_integral, projected_length = fields[segment_id]
        side = right_integral if right else projected_length - right_integral
        return (arc["length_m"] + strength * radial + side_strength * max(0.0, side)
                + reuse_strength * arc["length_m"] * (segment_id in used))

    candidates: list[RunCandidate] = []
    outcomes: list[Literal["complete", "unreachable", "deadline"]] = []
    for left in ((True,) if out_and_back else (True, False)):
        try:
            outbound = shortest_path(graph, overlay.home, overlay.turnaround,
                                     arc_cost=lambda arc: cost(arc, left, frozenset()),
                                     heuristic=heuristic, deadline=deadline)
            if outbound is None:
                outcomes.append("unreachable")
                continue
            used = frozenset(graph.arcs[a]["segment_id"] for a in outbound.arc_ids)
            returning = reverse_path(graph, outbound) if out_and_back else None
            if returning is None:
                returning = shortest_path(graph, overlay.turnaround, overlay.home,
                                      arc_cost=lambda arc: cost(arc, not left, used),
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

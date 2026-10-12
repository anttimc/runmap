"""Eligible edge snapping and budgeted outing inclusion over prepared topology."""

from collections import ChainMap, OrderedDict
from dataclasses import dataclass, field, replace
import math
from threading import RLock

from rasterio.warp import transform

from runmap.routing.profiles import arc_forward
from runmap.routing.search import arc_usable, shortest_distances, reachable_nodes


VIRTUAL_START = "@snapped-start"


@dataclass(frozen=True)
class Snap:
    segment_id: str
    graph_offset_m: float
    chainage_m: float
    longitude: float
    latitude: float
    offset_m: float


@dataclass(frozen=True)
class DistanceData:
    snap: Snap
    maximum_m: float
    outward: dict
    returning: dict
    outward_connected: set
    return_connected: set


@dataclass
class ExplorationCache:
    entries: OrderedDict = field(default_factory=OrderedDict)
    lock: object = field(default_factory=RLock)
    capacity: int = 8


def segment_reason(index, segment_id):
    if segment_id in index.ineligible_segments:
        return index.ineligible_segments[segment_id]
    if segment_id in index.boundary_blocked_segments:
        return "prepared_route_boundary"
    if not legal_directions(index, segment_id):
        return "no_legal_direction"
    return None


def snap_candidates(data, index, longitude, latitude, radius_m=50, preferred_segment=None):
    if not all(math.isfinite(v) for v in (longitude, latitude, radius_m)) or not (
        -180 <= longitude <= 180 and -85 <= latitude <= 85 and 0 < radius_m <= 200
    ):
        raise ValueError("Invalid snap location or radius (maximum 200 m)")
    # Same interval index as viewport delivery, with a conservative geographic envelope.
    delta_lat = radius_m / 100000
    delta_lon = delta_lat / max(.01, math.cos(math.radians(latitude)))
    cell_size = data.cell_degrees
    indices = set()
    for x in range(math.floor((longitude - delta_lon) / cell_size), math.floor((longitude + delta_lon) / cell_size) + 1):
        for y in range(math.floor((latitude - delta_lat) / cell_size), math.floor((latitude + delta_lat) / cell_size) + 1):
            indices.update(data.cells.get((x, y), ()))
    records = []
    reasons = {}
    for feature_index in indices:
        feature = data.features[feature_index]
        segment_id = feature["properties"]["segment_id"]
        if segment_id not in reasons:
            reasons[segment_id] = segment_reason(index, segment_id)
        if not reasons[segment_id]:
            interval = data.measurements.document["segments"][segment_id][feature["properties"]["interval_index"]]
            records.append(interval)
    if not records:
        return None, []
    coordinates = [(longitude, latitude)] + [point for item in records for point in item["geometry"]]
    xs, ys = transform("EPSG:4326", "EPSG:3067", *zip(*coordinates))
    px, py = xs[0], ys[0]
    choices = {}
    for i, item in enumerate(records):
        a, b = 2 * i + 1, 2 * i + 2
        dx, dy = xs[b] - xs[a], ys[b] - ys[a]
        length2 = dx * dx + dy * dy
        fraction = max(0.0, min(1.0, ((px - xs[a]) * dx + (py - ys[a]) * dy) / length2)) if length2 else 0
        x, y = xs[a] + fraction * dx, ys[a] + fraction * dy
        distance = math.hypot(x - px, y - py)
        if distance > radius_m:
            continue
        segment_id = item["segment_id"]
        if segment_id in choices and choices[segment_id].offset_m <= distance:
            continue
        lon, lat = transform("EPSG:3067", "EPSG:4326", [x], [y])
        choices[segment_id] = Snap(segment_id,
                                  item["graph_start_m"] + fraction * (item["graph_end_m"] - item["graph_start_m"]),
                                  item["start_m"] + fraction * (item["end_m"] - item["start_m"]),
                                  lon[0], lat[0], distance)
    candidates = sorted(choices.values(), key=lambda snap: (snap.offset_m, snap.segment_id))
    selected = choices.get(preferred_segment) if preferred_segment else (candidates[0] if candidates else None)
    alternatives = candidates[:8]
    if selected and selected not in alternatives:
        alternatives.append(selected)
    return selected, alternatives


def split_start_graph(index, snap):
    """Overlay temporary directed partial arcs; never modify prepared graph dictionaries."""
    segment = index.segments[snap.segment_id]
    a, b, length = segment["from"], segment["to"], segment["length_m"]
    # At an exact endpoint use the real node, preserving junction and access semantics.
    if snap.graph_offset_m <= 1e-8:
        return index, a
    if length - snap.graph_offset_m <= 1e-8:
        return index, b
    outgoing, incoming, arcs = {}, {}, {}
    for node in {a, b, VIRTUAL_START}:
        outgoing[node] = [arc_id for arc_id in index.outgoing.get(node, ()) if index.arcs[arc_id]["segment_id"] != snap.segment_id]
        incoming[node] = [arc_id for arc_id in index.incoming.get(node, ()) if index.arcs[arc_id]["segment_id"] != snap.segment_id]
    legal = legal_directions(index, snap.segment_id)
    for forward, first, second in ((True, a, VIRTUAL_START), (True, VIRTUAL_START, b),
                                   (False, b, VIRTUAL_START), (False, VIRTUAL_START, a)):
        if forward not in legal:
            continue
        left_part = a == first if forward else a == second
        part_length = snap.graph_offset_m if left_part else length - snap.graph_offset_m
        arc_id = f"{snap.segment_id}:split-{len(arcs)}:{'f' if forward else 'b'}"
        arcs[arc_id] = {"id": arc_id, "segment_id": snap.segment_id, "from": first, "to": second,
                        "length_m": part_length}
        outgoing[first].append(arc_id)
        incoming[second].append(arc_id)
    nodes = {VIRTUAL_START: {"id": VIRTUAL_START, "longitude": snap.longitude, "latitude": snap.latitude}}
    return replace(index, nodes=ChainMap(nodes, index.nodes), arcs=ChainMap(arcs, index.arcs),
                   outgoing=ChainMap(outgoing, index.outgoing), incoming=ChainMap(incoming, index.incoming)), VIRTUAL_START


def legal_directions(index, segment_id):
    segment = index.segments[segment_id]
    result = set()
    for node in {segment["from"], segment["to"]}:
        for arc_id in index.outgoing.get(node, ()):
            arc = index.arcs[arc_id]
            if arc["segment_id"] == segment_id and arc_usable(index, arc):
                result.add(arc_forward(segment, arc))
    return result


def distance_data(index, snap, maximum_m):
    graph, start = split_start_graph(index, snap)
    return DistanceData(snap, maximum_m,
                        shortest_distances(graph, start, maximum_m),
                        shortest_distances(graph, start, maximum_m, reverse=True),
                        reachable_nodes(graph, start), reachable_nodes(graph, start, reverse=True))


def cached_distances(cache, dataset_id, index, snap, maximum_m):
    key = (dataset_id, index.profile["id"], snap.segment_id, snap.graph_offset_m)
    # Serialize creation for one bounded cache, preventing duplicate large searches.
    with cache.lock:
        existing = cache.entries.get(key)
        if existing and existing.maximum_m >= maximum_m:
            cache.entries.move_to_end(key)
            return existing, True
        result = distance_data(index, snap, maximum_m)
        cache.entries[key] = result
        cache.entries.move_to_end(key)
        while len(cache.entries) > cache.capacity:
            cache.entries.popitem(last=False)
        return result, False


def _point_distance(index, distances, connected, snap, segment_id, offset, returning=False):
    segment = index.segments[segment_id]
    length = segment["length_m"]
    choices, possible = [], False
    # On the start segment, use the virtual start as a third endpoint. This also
    # handles legal interior turns without routing via an original endpoint.
    anchors = [(segment["from"], 0.0), (segment["to"], length)]
    if segment_id == snap.segment_id:
        anchors.append((VIRTUAL_START, snap.graph_offset_m))
    for node, position in anchors:
        for forward in legal_directions(index, segment_id):
            delta = offset - position
            can_travel = delta >= -1e-8 if forward else delta <= 1e-8
            if returning:
                can_travel = delta <= 1e-8 if forward else delta >= -1e-8
            if not can_travel:
                continue
            # Endpoint snaps use the actual node in Dijkstra, but this equivalent
            # zero-distance anchor remains useful for the point formulas.
            if node == VIRTUAL_START:
                base, is_connected = 0.0, True
            else:
                base, is_connected = distances.get(node, math.inf), node in connected
            possible |= is_connected
            choices.append(base + abs(delta))
    return min(choices, default=math.inf), possible


def interval_inclusion(index, distances, segment_id, start_m, end_m, budget_m):
    reason = segment_reason(index, segment_id)
    if reason:
        return {"inclusion_state": "excluded", "inclusion_reason": reason,
                "minimum_inclusion_m": None, "directions": []}
    results = []
    for forward in sorted(legal_directions(index, segment_id), reverse=True):
        a, b = (start_m, end_m) if forward else (end_m, start_m)
        outward, outward_possible = _point_distance(index, distances.outward, distances.outward_connected,
                                                     distances.snap, segment_id, a)
        returning, return_possible = _point_distance(index, distances.returning, distances.return_connected,
                                                      distances.snap, segment_id, b, returning=True)
        # A point minimum beyond the search radius is not numerically resolved:
        # an unvisited anchor could improve it while still exceeding the budget.
        if outward > distances.maximum_m:
            outward = math.inf
        if returning > distances.maximum_m:
            returning = math.inf
        total = outward + abs(end_m - start_m) + returning
        results.append({"direction": "forward" if forward else "backward",
                        "minimum_inclusion_m": total if math.isfinite(total) else None,
                        "connected": outward_possible and return_possible})
    finite = [item["minimum_inclusion_m"] for item in results if item["minimum_inclusion_m"] is not None]
    minimum = min(finite, default=None)
    connected = any(item["connected"] for item in results)
    state = "included" if minimum is not None and minimum <= budget_m + 1e-8 else ("beyond_budget" if connected else "no_connection")
    return {"inclusion_state": state,
            "inclusion_reason": {"included": "entire_interval_within_budget", "beyond_budget": "beyond_distance_budget",
                                 "no_connection": "no_connection_in_prepared_data"}[state],
            "minimum_inclusion_m": minimum, "distance_lower_bound_m": budget_m if minimum is None and connected else None,
            "directions": results}

"""Share eligible endpoint snapping and forward-distance inspection semantics."""

from collections import OrderedDict
from collections.abc import Sequence
from runmap.routing.types import Measurement
from concurrent.futures import Future
from dataclasses import dataclass, field
import math
from _thread import RLock
from rasterio.warp import transform
from runmap.paths.measurements import indexed_measurement
from runmap.routing.integer_graph import IntegerGraph
from runmap.routing.graph import GraphData
from runmap.routing.distance_types import NodeDistances


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
    outward: NodeDistances


@dataclass
class ExplorationCache:
    entries: OrderedDict[tuple[str, str, str, float], DistanceData] = field(default_factory=OrderedDict)
    lock: RLock = field(default_factory=RLock)
    capacity: int = 8
    pending: dict[tuple[str, str, str, float, float], Future[DistanceData]] = field(default_factory=dict)


def segment_reason(index, segment_id):
    if segment_id in index.ineligible_segments:
        return index.ineligible_segments[segment_id]
    if segment_id in index.boundary_blocked_segments:
        return "prepared_route_boundary"
    if not legal_directions(index, segment_id):
        return "no_legal_direction"
    return None


def snap_candidates(data, index, longitude, latitude, radius_m=50, preferred_segment=None):
    validate_snap(longitude, latitude, radius_m)
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
        interval = indexed_measurement(data.measurements, feature.segment, feature.interval_index)
        segment_id = interval["segment_id"]
        if segment_id not in reasons:
            reasons[segment_id] = segment_reason(index, segment_id)
        if not reasons[segment_id]:
            records.append(interval)
    return snap_measurements(records, longitude, latitude, radius_m, preferred_segment)


def validate_snap(longitude: float, latitude: float, radius_m: float) -> None:
    if not all(math.isfinite(v) for v in (longitude, latitude, radius_m)) or not (
        -180 <= longitude <= 180 and -85 <= latitude <= 85 and 0 < radius_m <= 200
    ):
        raise ValueError("Invalid snap location or radius (maximum 200 m)")


def snap_measurements(records: Sequence[Measurement], longitude: float, latitude: float,
                      radius_m: float, preferred_segment: str | None = None) -> tuple[Snap | None, list[Snap]]:
    if not records:
        return None, []
    coordinates = [(longitude, latitude)] + [point for item in records for point in item["geometry"]]
    xs, ys = transform("EPSG:4326", "EPSG:3067", *zip(*coordinates))
    px, py = xs[0], ys[0]
    choices: dict[str, Snap] = {}
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


def legal_directions(index: GraphData, segment_id: str) -> frozenset[bool]:
    return index.direction_cache[segment_id]


def _distance_index(index: GraphData, distances: DistanceData) -> IntegerGraph:
    dense = index.integer
    if dense is None or distances.outward.node_ids is not dense.node_ids:
        raise ValueError("Distances belong to a different prepared integer graph")
    return dense


def _point_distance(index: GraphData, dense: IntegerGraph, distances: NodeDistances,
                    snap: Snap, segment_id: str, offset: float) -> float:
    segment = index.segments[segment_id]
    length = segment["length_m"]
    choices: list[float] = []
    # On the start segment, use the virtual start as a third endpoint. This also
    # handles legal interior turns without routing via an original endpoint.
    anchors = [(segment["from"], 0.0), (segment["to"], length)]
    if segment_id == snap.segment_id:
        anchors.append((VIRTUAL_START, snap.graph_offset_m))
    for node, position in anchors:
        for forward in legal_directions(index, segment_id):
            delta = offset - position
            can_travel = delta >= -1e-8 if forward else delta <= 1e-8
            if not can_travel:
                continue
            # Endpoint snaps use the actual node in Dijkstra, but this equivalent
            # zero-distance anchor remains useful for the point formulas.
            if node == VIRTUAL_START:
                base = 0.0
            else:
                base = float(distances.values[dense.node_indices[node]])
            choices.append(base + abs(delta))
    return min(choices, default=math.inf)


def interval_inclusion(index: GraphData, distances: DistanceData, segment_id: str,
                        start_m: float, end_m: float, budget_m: float) -> dict[str, object]:
    dense = _distance_index(index, distances)
    reason = segment_reason(index, segment_id)
    if reason:
        return {"inclusion_state": "excluded", "inclusion_reason": reason,
                "minimum_inclusion_m": None, "directions": []}
    results = []
    for forward in sorted(legal_directions(index, segment_id), reverse=True):
        a, b = (start_m, end_m) if forward else (end_m, start_m)
        outward = _point_distance(index, dense, distances.outward, distances.snap, segment_id, a)
        # A point minimum beyond the search radius is not numerically resolved:
        # an unvisited anchor could improve it while still exceeding the budget.
        if outward > distances.maximum_m:
            outward = math.inf
        total = outward + abs(b - a)
        results.append({"direction": "forward" if forward else "backward",
                        "minimum_inclusion_m": total if math.isfinite(total) else None})
    finite = [item["minimum_inclusion_m"] for item in results if item["minimum_inclusion_m"] is not None]
    minimum = min(finite, default=None)
    state = "included" if minimum is not None and minimum <= budget_m + 1e-8 else "beyond_budget"
    return {"inclusion_state": state,
            "inclusion_reason": {"included": "entire_interval_within_budget", "beyond_budget": "beyond_distance_budget"}[state],
            "minimum_inclusion_m": minimum, "distance_lower_bound_m": budget_m if minimum is None else None,
            "directions": results}


def distance_from_start(index: GraphData, distances: DistanceData, segment_id: str, offset_m: float) -> float | None:
    """Shortest legal outward distance to a point, within the cached search bound."""
    dense = _distance_index(index, distances)
    if segment_reason(index, segment_id):
        return None
    value = _point_distance(index, dense, distances.outward, distances.snap, segment_id, offset_m)
    return value if math.isfinite(value) and value <= distances.maximum_m else None

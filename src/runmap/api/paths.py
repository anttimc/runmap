"""File-backed path exploration; requests never import OSM or sample terrain."""

from collections import defaultdict
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path

from rasterio.warp import transform

from runmap.paths.measurements import MeasurementData, graph_digest, inspect_measurements, load_measurements
from runmap.routing.profiles import surface_category, exploration_profile
from runmap.routing.search import GraphData, build_graph_index, apply_profile
from runmap.paths.exploration import (ExplorationCache, Snap, cached_distances, distance_from_start, interval_inclusion, legal_directions, segment_reason, snap_candidates)


MIN_ZOOM = 11
MAX_AREA_KM2 = 2500
MAX_FEATURES = 500000
CELL_DEGREES = 0.005


@dataclass(frozen=True)
class PathData:
    graph: dict
    measurements: MeasurementData
    segments: dict
    features: tuple
    cells: dict
    coverage: dict
    bounds: tuple
    dataset_id: str
    routing: GraphData
    profile_indexes: dict = field(default_factory=dict)
    boundary_intervals: dict = field(default_factory=dict)
    exploration_cache: ExplorationCache = field(default_factory=ExplorationCache)
    cell_degrees: float = CELL_DEGREES


def _bounds(coordinates):
    xs, ys = zip(*coordinates)
    return min(xs), min(ys), max(xs), max(ys)


def _cells(bounds):
    west, south, east, north = bounds
    for x in range(math.floor(west / CELL_DEGREES), math.floor(east / CELL_DEGREES) + 1):
        for y in range(math.floor(south / CELL_DEGREES), math.floor(north / CELL_DEGREES) + 1):
            yield x, y


def _overlap(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _line_intersects(coordinates, bounds):
    """Clip each line pair against a viewport, including its boundary."""
    west, south, east, north = bounds
    for first, second in zip(coordinates, coordinates[1:]):
        dx, dy = second[0] - first[0], second[1] - first[1]
        lo, hi = 0.0, 1.0
        for p, q in ((-dx, first[0] - west), (dx, east - first[0]),
                     (-dy, first[1] - south), (dy, north - first[1])):
            if p == 0:
                if q < 0:
                    break
            elif p < 0:
                lo = max(lo, q / p)
            else:
                hi = min(hi, q / p)
            if lo > hi:
                break
        else:
            return True
    return False


def _point_in_ring(point, ring):
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:]):
        cross = (x - a[0]) * (b[1] - a[1]) - (y - a[1]) * (b[0] - a[0])
        if abs(cross) < 1e-12 and min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1]):
            return True
        if (a[1] > y) != (b[1] > y):
            intersection = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < intersection:
                inside = not inside
    return inside


def _coverage(graph):
    bounds = graph.get("experiment", {}).get("bounds_epsg3067")
    if bounds:
        west, south, east, north = bounds
        lon, lat = transform("EPSG:3067", "EPSG:4326",
                             [west, east, east, west, west], [south, south, north, north, south])
        ring = [list(point) for point in zip(lon, lat)]
        return {"type": "Polygon", "coordinates": [ring]}, "prepared_extract"
    bounds = _bounds([point for segment in graph["segments"] for point in segment["geometry"]])
    west, south, east, north = bounds
    return {"type": "Polygon", "coordinates": [[
        [west, south], [east, south], [east, north], [west, north], [west, south],
    ]]}, "geometry_envelope"


def load_path_data(graph_path):
    graph_path = Path(graph_path)
    graph = json.loads(graph_path.read_text())
    if "measurements" not in graph:
        raise FileNotFoundError("Graph has no prepared measurement reference")
    measurements = load_measurements(graph_path)
    if measurements.document["graph_digest"] != graph_digest(graph):
        raise ValueError("Graph changed while loading measurements; restart the service")
    segments = {segment["id"]: segment for segment in graph["segments"]}
    features = []
    cells = defaultdict(list)
    for segment_id, intervals in measurements.document["segments"].items():
        category = surface_category(segments[segment_id])
        for interval in intervals:
            flags = interval["quality_flags"]
            quality = "missing" if not interval["valid"] else (
                "uncertain" if "bridge" in flags or "tunnel" in flags else "terrain_estimate")
            feature = {
                "type": "Feature", "id": f"{segment_id}:{interval['interval_index']}",
                "geometry": {"type": "LineString", "coordinates": interval["geometry"]},
                "properties": {
                    "segment_id": segment_id, "interval_index": interval["interval_index"],
                    "grade_percent": interval["grade_percent"], "quality": quality,
                    "quality_flags": flags, "surface_class": category,
                    "stairs": "stairs" in flags,
                },
            }
            feature_index = len(features)
            features.append(feature)
            for cell in _cells(_bounds(interval["geometry"])):
                cells[cell].append(feature_index)
    if not features:
        raise ValueError("Path measurements contain no display intervals")
    coverage, kind = _coverage(graph)
    return PathData(graph, measurements, segments, tuple(features), dict(cells),
                    {"geometry": coverage, "kind": kind},
                    _bounds(coverage["coordinates"][0]), graph_digest(graph), build_graph_index(graph))


def path_config(data):
    return {
        "status": "ready", "dataset_id": data.dataset_id,
        "coverage": data.coverage, "bounds_wgs84": data.bounds,
        "method": data.measurements.document["method"],
        "initial_view": {
            "longitude": data.graph.get("start", {}).get("longitude", (data.bounds[0] + data.bounds[2]) / 2),
            "latitude": data.graph.get("start", {}).get("latitude", (data.bounds[1] + data.bounds[3]) / 2),
            "zoom": 15,
        },
        "min_zoom": MIN_ZOOM, "max_area_km2": MAX_AREA_KM2, "max_features": MAX_FEATURES,
        "grade_bands_percent": [0, 2, 5, 8],
        "filters": ["all", "surfaced"],
        "snap_radius_m": 50, "default_budget_km": 6, "maximum_budget_km": 50,
        "importer_notes": [
            "Cycleways require explicit foot=yes/designated/permissive/official during import.",
            "Pedestrian areas are represented by their mapped perimeters, not area interiors.",
            "Unsupported highways, indoor/enclosed service and parking aisles are excluded during import.",
            "Display filters cannot recover paths excluded during import.",
        ],
        "surface_note": "Surface tags describe material, not proof of maintenance; unknowns are explicit.",
        "coverage_note": "Prepared candidate graph only; importer exclusions still apply.",
    }


def viewport_paths(data, bounds, zoom, surface="all", max_features=MAX_FEATURES, avoid_stairs=True):
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds):
        raise ValueError("Viewport needs four finite WGS84 bounds")
    west, south, east, north = bounds
    if not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
        raise ValueError("Invalid viewport bounds")
    if not math.isfinite(zoom) or not 0 <= zoom <= 24:
        raise ValueError("Invalid zoom")
    if surface not in {"all", "surfaced"}:
        raise ValueError("Unknown surface filter")
    ring = data.coverage["geometry"]["coordinates"][0]
    corners = [(west, south), (east, south), (east, north), (west, north)]
    inside = [_point_in_ring(point, ring) for point in corners]
    response = {"type": "FeatureCollection", "features": [], "dataset_id": data.dataset_id,
                "status": "ready", "coverage_limited": not all(inside)}
    if not _overlap(bounds, data.bounds) or not (any(inside) or _line_intersects(ring, bounds)):
        return {**response, "status": "outside_coverage"}
    area = (east - west) * (north - south) * 111.32 ** 2 * math.cos(math.radians((north + south) / 2))
    if zoom < MIN_ZOOM or area > MAX_AREA_KM2:
        return {**response, "status": "zoom_in", "reason": "zoom" if zoom < MIN_ZOOM else "area"}
    # Clip the index query to coverage, bounding the number of grid cells visited.
    clipped = (max(west, data.bounds[0]), max(south, data.bounds[1]),
               min(east, data.bounds[2]), min(north, data.bounds[3]))
    candidates = set()
    for cell in _cells(clipped):
        candidates.update(data.cells.get(cell, ()))
    policy_index = path_policy_index(data, surface, avoid_stairs)
    features = []
    reasons = {}
    for index in sorted(candidates):
        feature = data.features[index]
        segment_id = feature["properties"]["segment_id"]
        if segment_id not in reasons:
            reasons[segment_id] = segment_reason(policy_index, segment_id)
        reason = reasons[segment_id]
        # Keep stair and surface exclusions as inspectable contextual geometry.
        feature = {**feature, "properties": {**feature["properties"],
                   "eligibility_reason": reason, "eligible": reason is None}}
        coordinates = feature["geometry"]["coordinates"]
        in_coverage = any(_point_in_ring(point, ring) for point in coordinates)
        if not in_coverage and not _line_intersects(ring, _bounds(coordinates)):
            continue
        if _line_intersects(coordinates, bounds):
            features.append(feature)
            if len(features) > max_features:
                return {**response, "status": "zoom_in", "reason": "feature_limit"}
    return {**response, "features": features}


def uphill_sections(intervals):
    """Group strictly positive consecutive samples without crossing missing intervals."""
    sections = []
    current = None
    for item in intervals:
        if not item["valid"] or item["grade_percent"] <= 0:
            current = None
            continue
        if current is None:
            current = {"start_m": item["start_m"], "end_m": item["end_m"],
                       "length_m": 0.0, "gain_m": 0.0, "peak_grade_percent": 0.0,
                       "uncertain": False}
            sections.append(current)
        current["end_m"] = item["end_m"]
        current["length_m"] += item["end_m"] - item["start_m"]
        current["gain_m"] += item["height_change_m"]
        current["peak_grade_percent"] = max(current["peak_grade_percent"], item["grade_percent"])
        current["mean_grade_percent"] = 100 * current["gain_m"] / current["length_m"]
        current["uncertain"] |= bool({"bridge", "tunnel"}.intersection(item["quality_flags"]))
    return sections


def inspect_path(data, segment_id, reverse=False, surface="all", avoid_stairs=True):
    result = inspect_measurements(data.measurements, segment_id, reverse=reverse)
    policy_index = path_policy_index(data, surface, avoid_stairs)
    reason = segment_reason(policy_index, segment_id)
    directions = legal_directions(policy_index, segment_id) if reason is None else set()
    return {**result, "dataset_id": data.dataset_id, "segment": data.segments[segment_id],
            "traversal_permissions": {"forward": True in directions, "backward": False in directions},
            "eligible": reason is None,
            "eligibility_reason": reason,
            "surface_class": surface_category(data.segments[segment_id]),
            "source": data.graph.get("source", {}),
            "uphill_sections": uphill_sections(result["intervals"])}


def path_policy_index(data, surface="all", avoid_stairs=True):
    profile = exploration_profile(surface, avoid_stairs)
    with data.exploration_cache.lock:
        if profile["id"] not in data.profile_indexes:
            data.profile_indexes[profile["id"]] = apply_profile(data.routing, profile)
        return data.profile_indexes[profile["id"]]


def snap_path_start(data, longitude, latitude, surface="all", avoid_stairs=True,
                    radius_m=50, segment_id=None):
    index = path_policy_index(data, surface, avoid_stairs)
    snap, alternatives = snap_candidates(data, index, longitude, latitude, radius_m, segment_id)
    return {"dataset_id": data.dataset_id, "status": "ready" if snap else "no_eligible_snap",
            "requested": [longitude, latitude], "snap": asdict(snap) if snap else None,
            "candidates": [{**asdict(candidate), "description":
                " · ".join(str(value) for value in (
                    data.segments[candidate.segment_id]["tags"].get("name", candidate.segment_id),
                    data.segments[candidate.segment_id]["tags"].get("highway", "path"),
                    "bridge" if data.segments[candidate.segment_id]["tags"].get("bridge") not in (None, "no") else "",
                    "tunnel" if data.segments[candidate.segment_id]["tags"].get("tunnel") not in (None, "no") else "",
                ) if value)} for candidate in alternatives], "radius_m": radius_m,
            "offset_note": "The offset is visual distance, not a validated access route."}


def _near_boundary(data, geometry):
    # Conservative 100 m band; extract coverage may omit better return connections.
    ring = data.coverage["geometry"]["coordinates"][0]
    for x, y in geometry:
        if not _point_in_ring((x, y), ring):
            return True
        cos_lat = math.cos(math.radians(y))
        for a, b in zip(ring, ring[1:]):
            ax, ay = (a[0] - x) * cos_lat * 111320, (a[1] - y) * 111320
            bx, by = (b[0] - x) * cos_lat * 111320, (b[1] - y) * 111320
            dx, dy = bx - ax, by - ay
            fraction = max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0
            if math.hypot(ax + fraction * dx, ay + fraction * dy) <= 100:
                return True
    return False


def exploration_context(data, longitude, latitude, budget_km, surface, avoid_stairs, radius_m, segment_id):
    if not math.isfinite(budget_km) or not 0 < budget_km <= 50:
        raise ValueError("Budget must be greater than zero and at most 50 km")
    snap_response = snap_path_start(data, longitude, latitude, surface, avoid_stairs, radius_m, segment_id)
    index = path_policy_index(data, surface, avoid_stairs)
    if not snap_response["snap"]:
        return index, None, snap_response, False
    snap = Snap(**snap_response["snap"])
    distances, hit = cached_distances(data.exploration_cache, data.dataset_id, index, snap, budget_km * 1000)
    return index, distances, snap_response, hit


def _interval_state(data, index, distances, interval, budget_km):
    key = (interval["segment_id"], interval["interval_index"])
    if key not in data.boundary_intervals:
        data.boundary_intervals[key] = _near_boundary(data, interval["geometry"])
    return {**interval_inclusion(index, distances, interval["segment_id"],
                                interval["graph_start_m"], interval["graph_end_m"], budget_km * 1000),
            "coverage_limited": data.boundary_intervals[key],
            "distance_from_start_m": distance_from_start(index, distances, interval["segment_id"],
                (interval["graph_start_m"] + interval["graph_end_m"]) / 2)}


def explore_paths(data, bounds, zoom, longitude, latitude, budget_km=6, surface="all",
                  avoid_stairs=True, radius_m=50, segment_id=None):
    response = viewport_paths(data, bounds, zoom, surface, avoid_stairs=avoid_stairs)
    index, distances, snap_response, hit = exploration_context(
        data, longitude, latitude, budget_km, surface, avoid_stairs, radius_m, segment_id)
    response.update(start=snap_response, budget_km=budget_km,
                    label=f"Within {budget_km:g} km of the start",
                    semantics="Each entire highlighted interval is reachable within the forward distance budget; no return distance is included.")
    if distances is None:
        return {**response, "status": "no_eligible_snap", "features": []}
    features = []
    for feature in response["features"]:
        properties = feature["properties"]
        interval = data.measurements.document["segments"][properties["segment_id"]][properties["interval_index"]]
        features.append({**feature, "properties": {**properties, **_interval_state(data, index, distances, interval, budget_km)}})
    return {**response, "features": features, "distance_cache_hit": hit,
            "search_limit_m": distances.maximum_m, "interval_resolution_note":
            "Whole display intervals are tested conservatively. Exact minima beyond the bounded search may be unavailable; they are beyond budget, not disconnected."}


def inspect_outing(data, result, longitude, latitude, budget_km=6, surface="all", avoid_stairs=True,
                   radius_m=50, start_segment_id=None):
    index, distances, snap_response, _ = exploration_context(
        data, longitude, latitude, budget_km, surface, avoid_stairs, radius_m, start_segment_id)
    result = {**result, "start": snap_response, "budget_km": budget_km,
              "outing_label": f"Within {budget_km:g} km of the start"}
    if distances is None:
        return result
    original = data.measurements.document["segments"][result["segment_id"]]
    result["intervals"] = [{**interval, **_interval_state(data, index, distances, original[interval["interval_index"]], budget_km)}
                           for interval in result["intervals"]]
    return result


def render_paths(response, metric="gradient"):
    """Coalesce contiguous equal-value intervals; inspection retains exact samples.

    Keep segment boundaries and outing-state transitions, including disconnected
    and excluded portions. Only properties consumed by map styling are shipped.
    """
    if metric not in {"gradient", "distance"}:
        raise ValueError("Unknown path display metric")
    features = []
    previous_key = None
    previous_interval = None
    for feature in response["features"]:
        source = feature["properties"]
        grade = abs(source["grade_percent"] or 0)
        properties = {key: source[key] for key in (
            "segment_id", "quality", "stairs", "eligible", "inclusion_state"
        ) if key in source}
        if metric == "distance":
            distance = source.get("distance_from_start_m")
            properties["distance_from_start_m"] = distance
        else:
            properties["grade_percent"] = grade
        key = tuple(properties.items())
        coordinates = feature["geometry"]["coordinates"]
        interval = source["interval_index"]
        if (features and key == previous_key and interval == previous_interval + 1
                and features[-1]["geometry"]["coordinates"][-1] == coordinates[0]):
            features[-1]["geometry"]["coordinates"].extend(coordinates[1:])
        else:
            features.append({"type": "Feature", "id": feature["id"],
                             "geometry": {"type": "LineString", "coordinates": list(coordinates)},
                             "properties": properties})
        previous_key, previous_interval = key, interval
    return {**response, "features": features, "measurement_interval_count": len(response["features"])}

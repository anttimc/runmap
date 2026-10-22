"""Selective SQLite delivery; spatial refinement stays in the application."""

from dataclasses import asdict
import math

from rasterio.warp import transform_bounds, transform

from runmap.api.paths import (MAX_FEATURES, _bounds, _line_intersects, _overlap, _point_in_ring,
                              path_configuration, snap_response, uphill_sections, viewport_response)
from runmap.api.path_tiles import DisplayRun, Property, policy_options, variant_options
from runmap.api.sqlite_data import SQLitePathData
from runmap.paths.database import (iter_query_display_intervals, query_segment_inspection, query_segment_record)
from runmap.paths.display import measurement_feature, measurement_quality
from runmap.paths.exploration import snap_measurements, validate_snap
from runmap.paths.measurements import inspect_samples
from runmap.paths.preparation import PreparedDisplayInterval, PreparedPolicy
from runmap.routing.profiles import exploration_profile
from runmap.routing.types import Bounds


def database_path_config(data: SQLitePathData) -> dict[str, object]:
    view = data.presentation
    start = {key: value for key, value in (("longitude", view.longitude), ("latitude", view.latitude)) if value is not None}
    return path_configuration(data.dataset_id, data.coverage, data.bounds, data.method,
                               start, view.hidden_segments, view.hidden_intervals)


def _projected_candidates(data: SQLitePathData, bounds: Bounds) -> Bounds | None:
    if not _overlap(bounds, data.bounds):
        return None
    clipped = (max(bounds[0], data.bounds[0]), max(bounds[1], data.bounds[1]),
               min(bounds[2], data.bounds[2]), min(bounds[3], data.bounds[3]))
    return transform_bounds("EPSG:4326", "EPSG:3067", *clipped, densify_pts=21)


def database_viewport(data: SQLitePathData, bounds: Bounds, zoom: float, surface: str = "all",
                      avoid_stairs: bool = True, hide_building_access: bool = True,
                      max_features: int = MAX_FEATURES) -> dict[str, object]:
    response = viewport_response(data.coverage, data.bounds, data.dataset_id, bounds, zoom, surface)
    if response["status"] != "ready":
        return response
    projected = _projected_candidates(data, bounds)
    if projected is None:
        return response
    profile = exploration_profile(surface, avoid_stairs)
    ring = data.coverage["geometry"]["coordinates"][0]
    features = []
    with data.pool.connection() as connection:
        for stored in iter_query_display_intervals(connection, projected, profile["id"],
                                                    hide_building_access=hide_building_access):
            item = stored.measurement
            coordinates = item["geometry"]
            if not (any(_point_in_ring(point, ring) for point in coordinates)
                    or _line_intersects(ring, _bounds(coordinates))):
                continue
            if not _line_intersects(coordinates, bounds):
                continue
            feature = measurement_feature(item, stored.surface_class, stored.flat_min_grade_50m)
            reason = _policy(stored).reason
            features.append({**feature, "properties": {**feature["properties"],
                                                       "eligibility_reason": reason, "eligible": reason is None}})
            if len(features) > max_features:
                return {**response, "status": "zoom_in", "reason": "feature_limit"}
    return {**response, "features": features}


def database_inspection(data: SQLitePathData, segment_id: str, reverse: bool = False,
                        surface: str = "all", avoid_stairs: bool = True,
                        hide_building_access: bool = True) -> dict[str, object]:
    profile = exploration_profile(surface, avoid_stairs)
    with data.pool.connection() as connection:
        stored = query_segment_inspection(connection, segment_id, profile["id"])
    segment, classification, policy = stored.segment, stored.building_access, stored.policy
    result = inspect_samples(stored.measurements, segment_id, segment["length_m"], data.method, reverse)
    return {**result, "dataset_id": data.dataset_id, "segment": segment,
            "visibility_reason": classification.reason if classification else None,
            "hidden_by_building_access": hide_building_access and classification is not None,
            "building_access": asdict(classification) if classification else None,
            "traversal_permissions": {"forward": policy.forward, "backward": policy.backward},
            "eligible": policy.reason is None, "eligibility_reason": policy.reason,
            "surface_class": stored.surface_class, "source": data.presentation.source,
            "uphill_sections": uphill_sections(result["intervals"])}


def database_snap(data: SQLitePathData, longitude: float, latitude: float, surface: str = "all",
                   avoid_stairs: bool = True, radius_m: float = 50, segment_id: str | None = None) -> dict[str, object]:
    validate_snap(longitude, latitude, radius_m)
    profile = exploration_profile(surface, avoid_stairs)
    x, y = transform("EPSG:4326", "EPSG:3067", [longitude], [latitude])
    bounds = (x[0] - radius_m, y[0] - radius_m, x[0] + radius_m, y[0] + radius_m)
    with data.pool.connection() as connection:
        records = [stored.measurement for stored in iter_query_display_intervals(
            connection, bounds, profile["id"], hide_building_access=False) if _policy(stored).reason is None]
        snap, alternatives = snap_measurements(records, longitude, latitude, radius_m, segment_id)
        segments = {candidate.segment_id: query_segment_record(connection, candidate.segment_id) for candidate in alternatives}
    return snap_response(data.dataset_id, snap, alternatives, longitude, latitude, radius_m, segments)


def database_tile_runs(data: SQLitePathData, bounds: Bounds, policy: str, variant: str, z: int) -> list[DisplayRun]:
    metric, hidden = variant_options(variant)
    profile = exploration_profile(*policy_options(policy))
    projected = _projected_candidates(data, bounds)
    if projected is None:
        return []
    runs: list[DisplayRun] = []
    with data.pool.connection() as connection:
        for stored in iter_query_display_intervals(connection, projected, profile["id"], hide_building_access=hidden):
            properties = _tile_properties(stored, metric, z)
            coordinates = tuple((float(point[0]), float(point[1])) for point in stored.measurement["geometry"])
            interval = stored.measurement["interval_index"]
            if (runs and runs[-1].properties == properties and runs[-1].last_interval + 1 == interval
                    and runs[-1].coordinates[-1] == coordinates[0]):
                runs[-1] = DisplayRun(runs[-1].coordinates + coordinates[1:], properties, interval)
            else:
                runs.append(DisplayRun(coordinates, properties, interval))
    return runs


def _tile_properties(stored: PreparedDisplayInterval, metric: str, z: int) -> dict[str, Property]:
    item = stored.measurement
    flags = item["quality_flags"]
    quality = measurement_quality(item["valid"], flags)
    properties: dict[str, Property] = {"segment_id": item["segment_id"], "quality": quality,
                                      "stairs": "stairs" in flags, "eligible": _policy(stored).reason is None}
    if metric == "gradient":
        if stored.flat_min_grade_50m is not None:
            properties["flat_min_grade_50m"] = stored.flat_min_grade_50m
        grade = item["grade_percent"]
        if grade is not None and quality == "terrain_estimate":
            grade_bin = 2 if z <= 12 else 1 if z <= 14 else 0
            properties["grade_percent"] = math.ceil(abs(grade) / grade_bin) * grade_bin if grade_bin else abs(grade)
    return properties


def _policy(stored: PreparedDisplayInterval) -> PreparedPolicy:
    if stored.policy is None:
        raise ValueError("Prepared policy data is missing")
    return stored.policy

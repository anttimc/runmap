"""Selective SQLite delivery; spatial refinement stays in the application."""

from dataclasses import asdict
from collections.abc import Iterator
import math

from rasterio.warp import transform_bounds, transform

from runmap.api.paths import (MAX_FEATURES, _bounds, _line_intersects, _overlap, _point_in_ring,
                              path_configuration, snap_response, uphill_sections, viewport_response)
from runmap.api.path_tiles import ByteCache, DisplayRun, Property, Snapshot, policy_options, tile_hides_building_access
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_exploration import LocalExploration, retained_exploration_bytes
from runmap.paths.tile_inputs import QUALITIES, TileInputs, pack_tile_inputs
from runmap.paths.database import (iter_query_display_intervals, query_segment_inspection, query_segment_record,
                                   query_segment_endpoints, query_display_interval_ids, query_display_interval_batch)
from runmap.paths.display import measurement_feature
from runmap.paths.coverage import near_coverage_boundary
from runmap.paths.exploration import distance_from_start, interval_inclusion, snap_measurements, validate_snap
from runmap.paths.measurements import inspect_samples
from runmap.paths.preparation import PreparedDisplayInterval, PreparedPolicy, PreparedEndpoints
from runmap.routing.profiles import exploration_profile
from runmap.routing.numeric_exploration import (NumericDistanceData, numeric_interval_inclusion,
                                                numeric_segment_distances,
                                                numeric_distance_from_start)
from runmap.routing.types import Bounds, Measurement


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
        for stored in iter_query_display_intervals(
                connection, projected, profile["id"], hide_building_access=hide_building_access):
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
                   avoid_stairs: bool = True, radius_m: float = 50, segment_id: str | None = None,
                   *, deadline: float | None = None) -> dict[str, object]:
    validate_snap(longitude, latitude, radius_m)
    profile = exploration_profile(surface, avoid_stairs)
    x, y = transform("EPSG:4326", "EPSG:3067", [longitude], [latitude])
    bounds = (x[0] - radius_m, y[0] - radius_m, x[0] + radius_m, y[0] + radius_m)
    with data.pool.connection(deadline=deadline) as connection:
        records = [stored.measurement for stored in iter_query_display_intervals(
            connection, bounds, profile["id"], hide_building_access=False) if _policy(stored).reason is None]
        snap, alternatives = snap_measurements(records, longitude, latitude, radius_m, segment_id)
        segments = {candidate.segment_id: query_segment_record(connection, candidate.segment_id) for candidate in alternatives}
    return snap_response(data.dataset_id, snap, alternatives, longitude, latitude, radius_m, segments)


def database_tile_runs(data: SQLitePathData, bounds: Bounds, policy: str, variant: str, z: int,
                        *, input_cache: ByteCache[TileInputs] | None = None) -> list[DisplayRun]:
    hidden = tile_hides_building_access(variant)
    profile = exploration_profile(*policy_options(policy))
    projected = _projected_candidates(data, bounds)
    if projected is None:
        return []

    def prepare() -> TileInputs:
        return pack_tile_inputs(_tile_intervals(data, projected, profile["id"], hidden))

    if input_cache is None:
        inputs = prepare()
    else:
        key = repr((data.metadata.feature_identity, projected, profile["id"], hidden))
        inputs, _ = input_cache.compute(key, prepare, retained_exploration_bytes, allow_oversized=True)
    return _packed_tile_runs(inputs, z)


def _packed_tile_runs(inputs: TileInputs, z: int) -> list[DisplayRun]:
    runs: list[DisplayRun] = []
    for i in range(len(inputs.indices) // 2):
        segment_number, interval = inputs.indices[2*i:2*i+2]
        segment = inputs.segments[segment_number]
        properties = _packed_properties(inputs, i, z)
        lo, hi = inputs.geometry_offsets[i:i+2]
        coordinates = tuple((inputs.geometry[2*j], inputs.geometry[2*j+1]) for j in range(lo, hi))
        if (runs and runs[-1].properties == properties and runs[-1].last_interval + 1 == interval
                and runs[-1].coordinates[-1] == coordinates[0]):
            runs[-1] = DisplayRun(runs[-1].coordinates + coordinates[1:], properties, interval)
        else:
            runs.append(DisplayRun(coordinates, properties, interval))
    return runs


def _packed_properties(inputs: TileInputs, i: int, z: int) -> dict[str, Property]:
    segment = inputs.segments[inputs.indices[2*i]]
    quality = QUALITIES[inputs.flags[2*i]]
    properties: dict[str, Property] = {"segment_id": segment.id, "quality": quality,
        "stairs": bool(inputs.flags[2*i+1]), "eligible": segment.eligible}
    grade, flat = inputs.values[2*i:2*i+2]
    if not math.isnan(flat):
        properties["flat_min_grade_50m"] = flat
    if not math.isnan(grade) and quality == "terrain_estimate":
        grade_bin = 2 if z <= 12 else 1 if z <= 14 else 0
        properties["grade_percent"] = math.ceil(abs(grade) / grade_bin) * grade_bin if grade_bin else abs(grade)
    return properties


TILE_INTERVAL_BATCH_SIZE = 512


def _tile_intervals(data: SQLitePathData, bounds: Bounds, policy_id: str, hide_building_access: bool) -> Iterator[PreparedDisplayInterval]:
    """Release leases between bounded reads and before tile processing."""
    with data.pool.connection() as connection:
        numbers = query_display_interval_ids(connection, bounds, hide_building_access=hide_building_access)
    for offset in range(0, len(numbers), TILE_INTERVAL_BATCH_SIZE):
        with data.pool.connection() as connection:
            batch = query_display_interval_batch(connection, numbers[offset:offset+TILE_INTERVAL_BATCH_SIZE],
                                                  policy_id)
        yield from batch


def database_interval_state(data: SQLitePathData, snapshot: Snapshot, item: Measurement,
                             policy: PreparedPolicy, endpoints: PreparedEndpoints | None = None) -> dict[str, object]:
    limited = near_coverage_boundary(data.coverage, item["geometry"])
    return {**_interval_distances(snapshot, item["segment_id"], item["graph_start_m"], item["graph_end_m"],
                                  policy, endpoints), "coverage_limited": limited}


def _interval_distances(snapshot: Snapshot, key: str, start_m: float, end_m: float, policy: PreparedPolicy,
                        endpoints: PreparedEndpoints | None) -> dict[str, object]:
    if isinstance(snapshot.distances, NumericDistanceData):
        if endpoints is None:
            raise ValueError("Distance inspection requires prepared endpoints")
        prepared = numeric_segment_distances(snapshot.distances, key, endpoints, policy)
        state = numeric_interval_inclusion(snapshot.distances, key, endpoints, policy,
            start_m, end_m, snapshot.budget_km*1000, prepared=prepared)
        distance = numeric_distance_from_start(snapshot.distances, key, endpoints, policy,
            (start_m+end_m)/2, prepared=prepared)
        return {**state, "distance_from_start_m": distance}
    local = snapshot.local
    if not isinstance(local, LocalExploration):
        raise ValueError("SQLite snapshot has no local graph")
    if key in local.routing.segments:
        state = interval_inclusion(local.routing, snapshot.distances, key,
                                   start_m, end_m, snapshot.budget_km * 1000)
        distance = distance_from_start(local.routing, snapshot.distances, key,
                                        (start_m + end_m) / 2)
    elif policy.reason is not None:
        state = {"inclusion_state": "excluded", "inclusion_reason": policy.reason,
                 "minimum_inclusion_m": None, "directions": []}
        distance = None
    else:
        state = {"inclusion_state": "beyond_budget", "inclusion_reason": "beyond_distance_budget",
                 "minimum_inclusion_m": None, "distance_lower_bound_m": snapshot.budget_km * 1000,
                 "directions": [{"direction": direction, "minimum_inclusion_m": None}
                                for direction, allowed in (("forward", policy.forward), ("backward", policy.backward)) if allowed]}
        distance = None
    return {**state, "distance_from_start_m": distance}


def database_inspect_outing(data: SQLitePathData, result: dict[str, object], prepared: dict[str, object],
                             snapshot: Snapshot | None, budget_km: float, surface: str,
                             avoid_stairs: bool) -> dict[str, object]:
    result = {**result, "start": prepared.get("start", prepared), "budget_km": budget_km,
              "outing_label": f"Within {budget_km:g} km of the start"}
    if snapshot is None:
        return result
    profile = exploration_profile(surface, avoid_stairs)
    with data.pool.connection() as connection:
        stored = query_segment_inspection(connection, result["segment_id"], profile["id"])
        endpoints = (query_segment_endpoints(connection, result["segment_id"])
                     if isinstance(snapshot.distances, NumericDistanceData) else None)
    result["intervals"] = [{**interval, **database_interval_state(
        data, snapshot, stored.measurements[interval["interval_index"]], stored.policy, endpoints)} for interval in result["intervals"]]
    return result


def _policy(stored: PreparedDisplayInterval) -> PreparedPolicy:
    if stored.policy is None:
        raise ValueError("Prepared policy data is missing")
    return stored.policy

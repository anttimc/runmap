"""Display-only generalization and process-local exploration snapshots."""

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from hashlib import sha256
import math
from sys import getsizeof
from threading import RLock
from time import monotonic, time
from typing import Callable, Generic, TypeVar
from uuid import uuid4

import mapbox_vector_tile
import numpy as np
from shapely.geometry import LineString, box
from shapely import transform

from runmap.api.paths import PathData, _interval_state, candidate_path_indexes, display_segment_reason, exploration_context, path_policy_index
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_exploration import (ExplorationLimits, LocalExploration,
                                          prepare_local_exploration, retained_exploration_bytes)
from runmap.paths.display import measurement_quality
from runmap.routing.types import MeasurementLocation
from runmap.routing.profiles import exploration_profile
from runmap.paths.dead_ends import ACCESS_POLICY, CLASSIFIER_VERSION, ENTRANCE_LIMIT_M
from runmap.paths.exploration import DistanceData, Snap, segment_reason

RENDER_VERSION = 3
ALGORITHM_VERSION = 4
MIN_ZOOM = 10
MAX_ZOOM = 16
EXTENT = 4096
BUFFER = 8 * EXTENT / 512
Property = str | float | bool | int
T = TypeVar("T")


@dataclass(frozen=True)
class CacheEntry(Generic[T]):
    value: T
    size: int
    expires: float


class ByteCache(Generic[T]):
    """Coalesce identical work without holding a cache lock during computation."""

    def __init__(self, capacity: int, ttl: float) -> None:
        self.capacity = capacity
        self.ttl = ttl
        self.entries: OrderedDict[str, CacheEntry[T]] = OrderedDict()
        self.pending: dict[str, Future[T]] = {}
        self.lock = RLock()
        self.bytes = 0

    def _prune(self) -> None:
        for key in list(self.entries):
            if self.entries[key].expires <= monotonic():
                self.bytes -= self.entries.pop(key).size

    def get(self, key: str) -> T | None:
        with self.lock:
            self._prune()
            entry = self.entries.get(key)
            if entry is None:
                return None
            self.entries.move_to_end(key)
            return entry.value

    def compute(self, key: str, factory: Callable[[], T], size: Callable[[T], int],
                *, deadline: float | None = None) -> tuple[T, bool]:
        with self.lock:
            existing = self.get(key)
            if existing is not None:
                return existing, True
            future = self.pending.get(key)
            owner = future is None
            if future is None:
                future = Future()
                self.pending[key] = future
        if not owner:
            return future.result(timeout=None if deadline is None else max(0, deadline - monotonic())), True
        try:
            result = factory()
            retained = size(result)
            if retained > self.capacity:
                raise ValueError("Prepared result exceeds configured cache memory budget")
            with self.lock:
                self._prune()
                while self.entries and self.bytes + retained > self.capacity:
                    self.bytes -= self.entries.popitem(last=False)[1].size
                self.entries[key] = CacheEntry(result, retained, monotonic() + self.ttl)
                self.bytes += retained
                future.set_result(result)
            return result, False
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)


@dataclass(frozen=True)
class Snapshot:
    id: str
    distances: DistanceData
    budget_km: float
    policy: str
    expires_at: float
    preparation_ms: float
    routing_ms: float
    retained_bytes: int
    local: LocalExploration | None = None


def snapshot_distances(distances: DistanceData) -> tuple[DistanceData, int]:
    """Share sealed search buffers and charge their payload to each snapshot."""
    outward = distances.outward
    retained = (getsizeof(outward) + getsizeof(outward.values) + getsizeof(outward.values.obj)
                + getsizeof(outward.reached) + getsizeof(outward.reached.obj) + 512)
    return distances, retained


@dataclass(frozen=True)
class DisplayRun:
    coordinates: tuple[tuple[float, float], ...]
    properties: dict[str, Property]
    last_interval: int


def measurement_identity(data: PathData | SQLitePathData) -> str:
    return data.measurement_sha256 if isinstance(data, SQLitePathData) else data.graph["measurements"]["sha256"]


def render_version(data: PathData | SQLitePathData) -> str:
    identity: tuple[object, ...] = (measurement_identity(data), ACCESS_POLICY, CLASSIFIER_VERSION, ENTRANCE_LIMIT_M)
    if isinstance(data, SQLitePathData):
        identity = (*identity, data.metadata.feature_identity)
    digest = sha256(repr(identity).encode()).hexdigest()[:32]
    return f"{RENDER_VERSION}-{digest}"


def policy_options(policy: str) -> tuple[str, bool]:
    if policy not in {"all-stairs", "all-no-stairs", "surfaced-stairs", "surfaced-no-stairs"}:
        raise ValueError("Unknown path policy")
    return policy.split("-")[0], policy.endswith("no-stairs")


def variant_options(variant: str) -> tuple[str, bool]:
    if variant not in {"gradient-hidden", "gradient-all", "distance-hidden", "distance-all"}:
        raise ValueError("Unknown path tile variant")
    metric, visibility = variant.split("-")
    return metric, visibility == "hidden"


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    if not 0 <= z <= MAX_ZOOM or not (0 <= x < 1 << z and 0 <= y < 1 << z):
        raise ValueError("Invalid XYZ tile coordinate")
    n = 1 << z
    west, east = x / n * 360 - 180, (x + 1) / n * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return west, south, east, north


def projected_point(longitude: float, latitude: float, z: int, x: int, y: int) -> tuple[float, float]:
    latitude = max(-85.05112878, min(85.05112878, latitude))
    scale = (1 << z) * EXTENT
    return ((longitude + 180) / 360 * scale - x * EXTENT,
            (1 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2 * scale - y * EXTENT)


def display_runs(data: PathData, indexes: list[int], policy: str, variant: str,
                 z: int, snapshot: Snapshot | None) -> list[DisplayRun]:
    metric, _ = variant_options(variant)
    options = policy_options(policy)
    profile = exploration_profile(*options)
    index = path_policy_index(data, *options) if snapshot is not None else data.routing
    grade_bin = 2 if z <= 12 else 1 if z <= 14 else 0
    distance_bin = 250 if z <= 12 else 125 if z <= 14 else 0
    runs: list[DisplayRun] = []
    eligible: dict[str, bool] = {}
    measurements = data.measurements
    offsets, values = memoryview(measurements.interval_offsets), memoryview(measurements.values)
    valid, present = memoryview(measurements.valid), memoryview(measurements.present)
    flag_codes = memoryview(measurements.flag_codes)
    geometry, geometry_offsets = memoryview(measurements.geometry), memoryview(measurements.geometry_offsets)
    for i in indexes:
        feature = data.features[i]
        number = offsets[feature.segment] + feature.interval_index
        segment = measurements.segment_ids[feature.segment]
        flags = measurements.flag_values[flag_codes[number]]
        quality = measurement_quality(valid[number], flags)
        coordinates = _interval_coordinates(geometry, geometry_offsets, number)
        if segment not in eligible:
            eligible[segment] = (segment_reason(index, segment) if snapshot is not None
                                 else display_segment_reason(index, segment, profile)) is None
        properties: dict[str, Property] = {
            "segment_id": segment, "quality": quality, "stairs": "stairs" in flags,
            "eligible": eligible[segment],
        }
        distance: float | None = None
        if snapshot is not None:
            source = MeasurementLocation(segment_id=segment, interval_index=feature.interval_index,
                                         graph_start_m=values[number, 2], graph_end_m=values[number, 3],
                                         geometry=list(coordinates))
            state = _interval_state(data, index, snapshot.distances, source, snapshot.budget_km)
            properties["inclusion_state"] = state["inclusion_state"]
            properties["coverage_limited"] = state["coverage_limited"]
            distance = state["distance_from_start_m"]
        if metric == "gradient":
            if feature.flat_min_grade_50m is not None:
                properties["flat_min_grade_50m"] = feature.flat_min_grade_50m
            grade = values[number, 4] if present[number, 0] else None
            if grade is not None and quality == "terrain_estimate":
                properties["grade_percent"] = math.ceil(abs(grade) / grade_bin) * grade_bin if grade_bin else abs(grade)
        elif distance is not None:
            properties["distance_from_start_m"] = round(distance / distance_bin) * distance_bin if distance_bin else distance
        interval = feature.interval_index
        if (runs and runs[-1].properties == properties and runs[-1].last_interval + 1 == interval
                and runs[-1].coordinates[-1] == coordinates[0]):
            runs[-1] = DisplayRun(runs[-1].coordinates + coordinates[1:], properties, interval)
        else:
            runs.append(DisplayRun(coordinates, properties, interval))
    return runs


def _interval_coordinates(geometry: memoryview, offsets: memoryview, number: int) -> tuple[tuple[float, float], ...]:
    """Read native scalars directly without temporary NumPy rows or JSON lists."""
    return tuple((geometry[i, 0], geometry[i, 1]) for i in range(offsets[number], offsets[number + 1]))


def encode_tile(data: PathData | SQLitePathData, policy: str, variant: str, z: int, x: int, y: int,
                snapshot: Snapshot | None = None) -> bytes:
    west, south, east, north = tile_bounds(z, x, y)
    _, hidden = variant_options(variant)
    policy_options(policy)
    runs: list[DisplayRun] = []
    if z >= MIN_ZOOM:
        # Latitude expansion is conservative because Mercator's scale grows poleward.
        pad = (east - west) * BUFFER / EXTENT
        bounds = (west - pad, south - pad, east + pad, north + pad)
        if isinstance(data, SQLitePathData):
            from runmap.api.sqlite_paths import database_tile_runs
            runs = database_tile_runs(data, bounds, policy, variant, z, snapshot)
        else:
            indexes = candidate_path_indexes(data, bounds, hidden)
            runs = display_runs(data, indexes, policy, variant, z, snapshot)
    return encode_runs(runs, z, x, y)


def encode_runs(runs: list[DisplayRun], z: int, x: int, y: int) -> bytes:
    features = []
    clip = box(-BUFFER, -BUFFER, EXTENT + BUFFER, EXTENT + BUFFER)
    tolerance = EXTENT / 512 * (1 if z <= 12 else .5 if z <= 14 else .125)
    for run in runs:
        line = LineString([projected_point(lon, lat, z, x, y) for lon, lat in run.coordinates])
        geometry = line.simplify(tolerance).intersection(clip)
        if geometry.is_empty or geometry.geom_type not in {"LineString", "MultiLineString"}:
            continue
        geometry = transform(geometry, np.rint)
        if geometry.length == 0:
            continue
        features.append({"geometry": geometry, "properties": run.properties})
    return mapbox_vector_tile.encode({"name": "paths", "features": features},
                                    default_options={"y_coord_down": True, "extents": EXTENT})


class PathTileService:
    """Own cache lifetimes for one loaded dataset and one API worker."""

    def __init__(self, data: PathData | SQLitePathData, tile_bytes: int = 64 * 1024 ** 2,
                 snapshot_bytes: int = 128 * 1024 ** 2, snapshot_ttl: float = 900,
                 tile_ttl: float = 900, preparation_deadline: float = 10,
                 exploration_limits: ExplorationLimits = ExplorationLimits()) -> None:
        if (exploration_limits.max_area_km2 <= 0
                or exploration_limits.max_segments <= 0 or exploration_limits.max_retries < 0):
            raise ValueError("Invalid exploration limits")
        self.data = data
        self.tiles: ByteCache[bytes] = ByteCache(tile_bytes, tile_ttl)
        self.snapshots: ByteCache[Snapshot] = ByteCache(snapshot_bytes, snapshot_ttl)
        self.preparation_deadline = preparation_deadline
        self.exploration_limits = exploration_limits

    def prepare(self, longitude: float, latitude: float, budget_km: float, surface: str,
                avoid_stairs: bool, radius_m: float, segment_id: str | None) -> dict:
        started = monotonic()
        policy = f"{surface}-{'no-stairs' if avoid_stairs else 'stairs'}"
        policy_options(policy)
        if isinstance(self.data, SQLitePathData):
            return self._prepare_database(longitude, latitude, budget_km, surface, avoid_stairs,
                                           radius_m, segment_id, started, policy)
        _, distances, start, distance_hit = exploration_context(
            self.data,
            longitude, latitude, budget_km, surface, avoid_stairs, radius_m, segment_id,
            deadline=started + self.preparation_deadline)
        if distances is None:
            return start
        routing_ms = (monotonic() - started) * 1000
        snap = start["snap"]
        key = sha256(repr((self.data.dataset_id, measurement_identity(self.data), ALGORITHM_VERSION, policy,
                          snap["segment_id"], snap["graph_offset_m"], budget_km)).encode()).hexdigest()

        def materialize() -> Snapshot:
            frozen, retained = snapshot_distances(distances)
            if monotonic() - started > self.preparation_deadline:
                raise TimeoutError("Path preparation exceeded its deadline; try a smaller budget")
            return Snapshot(uuid4().hex, frozen, budget_km, policy,
                            time() + self.snapshots.ttl, (monotonic() - started) * 1000,
                            routing_ms, retained)

        snapshot, hit = self.snapshots.compute(key, materialize, lambda item: item.retained_bytes,
                                                deadline=started + self.preparation_deadline)
        return {"status": "ready", "dataset_id": self.data.dataset_id, "snapshot_id": snapshot.id,
                "start": start, "coverage": self.data.coverage, "bounds_wgs84": self.data.bounds,
                "tile_template": tile_template(self.data, f"snapshots/{snapshot.id}"),
                "expires_at": snapshot.expires_at, "preparation_ms": snapshot.preparation_ms,
                "request_ms": (monotonic() - started) * 1000, "retained_bytes": snapshot.retained_bytes,
                "routing_ms": snapshot.routing_ms, "interval_preparation_ms": 0.0,
                "snapshot_cache_hit": hit, "distance_cache_hit": distance_hit}

    def _prepare_database(self, longitude: float, latitude: float, budget_km: float, surface: str,
                           avoid_stairs: bool, radius_m: float, segment_id: str | None,
                           started: float, policy: str) -> dict[str, object]:
        from runmap.api.sqlite_paths import database_snap
        data = self.data
        assert isinstance(data, SQLitePathData)
        if not math.isfinite(budget_km) or not 0 < budget_km <= 50:
            raise ValueError("Budget must be greater than zero and at most 50 km")
        start = database_snap(data, longitude, latitude, surface, avoid_stairs, radius_m, segment_id,
                               deadline=started + self.preparation_deadline)
        if start["snap"] is None:
            return start
        snap = Snap(**start["snap"])
        key = repr((data.metadata.feature_identity, ALGORITHM_VERSION, policy, snap.segment_id,
                    snap.graph_offset_m, budget_km))

        def materialize() -> Snapshot:
            local = prepare_local_exploration(data, snap, budget_km, surface, avoid_stairs,
                                               started + self.preparation_deadline, self.exploration_limits)
            retained = retained_exploration_bytes(local) + 1024
            if monotonic() - started > self.preparation_deadline:
                raise TimeoutError("Path preparation exceeded its deadline; try a smaller budget")
            elapsed = (monotonic() - started) * 1000
            return Snapshot(uuid4().hex, local.distances, budget_km, policy, time() + self.snapshots.ttl,
                            elapsed, elapsed, retained, local)

        snapshot, hit = self.snapshots.compute(key, materialize, lambda item: item.retained_bytes,
                                                deadline=started + self.preparation_deadline)
        assert snapshot.local is not None
        return {"status": "ready", "dataset_id": data.dataset_id, "snapshot_id": snapshot.id,
                "start": start, "coverage": data.coverage, "bounds_wgs84": data.bounds,
                "tile_template": tile_template(data, f"snapshots/{snapshot.id}"),
                "expires_at": snapshot.expires_at, "preparation_ms": snapshot.preparation_ms,
                "request_ms": (monotonic() - started) * 1000, "retained_bytes": snapshot.retained_bytes,
                "routing_ms": snapshot.routing_ms, "interval_preparation_ms": 0.0,
                "snapshot_cache_hit": hit, "distance_cache_hit": hit,
                "search_bounds_epsg3067": snapshot.local.bounds, "search_retries": snapshot.local.retries}

    def snapshot(self, snapshot_id: str) -> Snapshot:
        with self.snapshots.lock:
            self.snapshots._prune()
            for key, entry in self.snapshots.entries.items():
                if entry.value.id == snapshot_id:
                    self.snapshots.entries.move_to_end(key)
                    return entry.value
        raise KeyError("Exploration snapshot expired; prepare it again")

    def tile(self, policy: str, variant: str, z: int, x: int, y: int,
             snapshot: Snapshot | None = None) -> tuple[bytes, bool, float]:
        started = monotonic()
        tile_bounds(z, x, y)
        variant_options(variant)
        policy_options(policy)
        identity = (render_version(self.data), self.data.dataset_id, CLASSIFIER_VERSION, ENTRANCE_LIMIT_M,
                    snapshot.id if snapshot else policy, variant, z, x, y)
        encoded, hit = self.tiles.compute(repr(identity),
            lambda: encode_tile(self.data, policy, variant, z, x, y, snapshot), len)
        return encoded, hit, (monotonic() - started) * 1000


def tile_template(data: PathData | SQLitePathData, context: str) -> str:
    return f"/api/paths/tiles/v{render_version(data)}/{data.dataset_id}/{context}/{{variant}}/{{z}}/{{x}}/{{y}}.mvt"

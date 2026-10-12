"""Display-only generalization and process-local exploration snapshots."""

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from hashlib import sha256
import math
from threading import RLock
from time import monotonic, time
from typing import Callable, Generic, TypeVar
from uuid import uuid4

import mapbox_vector_tile
import numpy as np
from numpy.typing import NDArray
from shapely.geometry import LineString, box
from shapely import transform

from runmap.api.paths import PathData, _interval_state, candidate_path_indexes, exploration_context, path_policy_index
from runmap.paths.dead_ends import ACCESS_POLICY, CLASSIFIER_VERSION, ENTRANCE_LIMIT_M
from runmap.paths.exploration import segment_reason

RENDER_VERSION = 1
ALGORITHM_VERSION = 1
MIN_ZOOM = 10
MAX_ZOOM = 16
EXTENT = 4096
BUFFER = 8 * EXTENT / 512
STATES = ("included", "beyond_budget", "no_connection", "excluded")
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

    def compute(self, key: str, factory: Callable[[], T], size: Callable[[T], int]) -> tuple[T, bool]:
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
            return future.result(), True
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
    state: NDArray[np.uint8]
    distance: NDArray[np.float64]
    boundary: NDArray[np.bool_]
    policy: str
    expires_at: float
    preparation_ms: float
    routing_ms: float
    interval_preparation_ms: float

    @property
    def retained_bytes(self) -> int:
        return self.state.nbytes + self.distance.nbytes + self.boundary.nbytes + 512


@dataclass(frozen=True)
class DisplayRun:
    coordinates: tuple[tuple[float, float], ...]
    properties: dict[str, Property]
    last_interval: int


def render_version(data: PathData) -> str:
    identity = (data.graph["measurements"]["sha256"], ACCESS_POLICY, CLASSIFIER_VERSION, ENTRANCE_LIMIT_M)
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
    index = path_policy_index(data, *policy_options(policy))
    grade_bin = 2 if z <= 12 else 1 if z <= 14 else 0
    distance_bin = 250 if z <= 12 else 125 if z <= 14 else 0
    runs: list[DisplayRun] = []
    for i in indexes:
        feature = data.features[i]
        source = feature["properties"]
        segment = source["segment_id"]
        properties: dict[str, Property] = {
            "segment_id": segment, "quality": source["quality"], "stairs": source["stairs"],
            "eligible": segment_reason(index, segment) is None,
        }
        if snapshot is not None:
            properties["inclusion_state"] = STATES[int(snapshot.state[i])]
            properties["coverage_limited"] = bool(snapshot.boundary[i])
        if metric == "gradient":
            grade = source["grade_percent"]
            if grade is not None and source["quality"] == "terrain_estimate":
                properties["grade_percent"] = math.ceil(abs(grade) / grade_bin) * grade_bin if grade_bin else abs(grade)
        elif snapshot is not None and math.isfinite(snapshot.distance[i]):
            distance = float(snapshot.distance[i])
            properties["distance_from_start_m"] = round(distance / distance_bin) * distance_bin if distance_bin else distance
        coordinates = tuple(tuple(point) for point in feature["geometry"]["coordinates"])
        interval = source["interval_index"]
        if (runs and runs[-1].properties == properties and runs[-1].last_interval + 1 == interval
                and runs[-1].coordinates[-1] == coordinates[0]):
            runs[-1] = DisplayRun(runs[-1].coordinates + coordinates[1:], properties, interval)
        else:
            runs.append(DisplayRun(coordinates, properties, interval))
    return runs


def encode_tile(data: PathData, policy: str, variant: str, z: int, x: int, y: int,
                snapshot: Snapshot | None = None) -> bytes:
    west, south, east, north = tile_bounds(z, x, y)
    _, hidden = variant_options(variant)
    policy_options(policy)
    features = []
    if z >= MIN_ZOOM:
        # Latitude expansion is conservative because Mercator's scale grows poleward.
        pad = (east - west) * BUFFER / EXTENT
        bounds = (west - pad, south - pad, east + pad, north + pad)
        indexes = candidate_path_indexes(data, bounds, hidden)
        clip = box(-BUFFER, -BUFFER, EXTENT + BUFFER, EXTENT + BUFFER)
        tolerance = EXTENT / 512 * (1 if z <= 12 else .5 if z <= 14 else .125)
        for run in display_runs(data, indexes, policy, variant, z, snapshot):
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

    def __init__(self, data: PathData, tile_bytes: int = 64 * 1024 ** 2,
                 snapshot_bytes: int = 128 * 1024 ** 2, snapshot_ttl: float = 900,
                 tile_ttl: float = 900, preparation_deadline: float = 10) -> None:
        self.data = data
        self.tiles: ByteCache[bytes] = ByteCache(tile_bytes, tile_ttl)
        self.snapshots: ByteCache[Snapshot] = ByteCache(snapshot_bytes, snapshot_ttl)
        self.preparation_deadline = preparation_deadline

    def prepare(self, longitude: float, latitude: float, budget_km: float, surface: str,
                avoid_stairs: bool, radius_m: float, segment_id: str | None) -> dict:
        started = monotonic()
        policy = f"{surface}-{'no-stairs' if avoid_stairs else 'stairs'}"
        policy_options(policy)
        index, distances, start, distance_hit = exploration_context(
            self.data, longitude, latitude, budget_km, surface, avoid_stairs, radius_m, segment_id,
            deadline=started + self.preparation_deadline)
        if distances is None:
            return start
        routing_ms = (monotonic() - started) * 1000
        snap = start["snap"]
        key = sha256(repr((self.data.dataset_id, self.data.graph["measurements"]["sha256"], ALGORITHM_VERSION, policy,
                          snap["segment_id"], snap["graph_offset_m"], budget_km)).encode()).hexdigest()

        def materialize() -> Snapshot:
            interval_started = monotonic()
            count = len(self.data.features)
            states = np.empty(count, dtype=np.uint8)
            values = np.full(count, np.nan, dtype=np.float64)
            boundary = np.empty(count, dtype=np.bool_)
            for i, feature in enumerate(self.data.features):
                if i % 256 == 0 and monotonic() - started > self.preparation_deadline:
                    raise TimeoutError("Path preparation exceeded its deadline; try a smaller budget")
                props = feature["properties"]
                interval = self.data.measurements.document["segments"][props["segment_id"]][props["interval_index"]]
                state = _interval_state(self.data, index, distances, interval, budget_km)
                states[i] = STATES.index(state["inclusion_state"])
                values[i] = state["distance_from_start_m"] if state["distance_from_start_m"] is not None else np.nan
                boundary[i] = state["coverage_limited"]
            for array in (states, values, boundary):
                array.flags.writeable = False
            return Snapshot(uuid4().hex, states, values, boundary, policy,
                            time() + self.snapshots.ttl, (monotonic() - started) * 1000,
                            routing_ms, (monotonic() - interval_started) * 1000)

        snapshot, hit = self.snapshots.compute(key, materialize, lambda item: item.retained_bytes)
        return {"status": "ready", "dataset_id": self.data.dataset_id, "snapshot_id": snapshot.id,
                "start": start, "coverage": self.data.coverage, "bounds_wgs84": self.data.bounds,
                "tile_template": tile_template(self.data, f"snapshots/{snapshot.id}"),
                "expires_at": snapshot.expires_at, "preparation_ms": snapshot.preparation_ms,
                "request_ms": (monotonic() - started) * 1000, "retained_bytes": snapshot.retained_bytes,
                "routing_ms": snapshot.routing_ms, "interval_preparation_ms": snapshot.interval_preparation_ms,
                "snapshot_cache_hit": hit, "distance_cache_hit": distance_hit}

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
        identity = (RENDER_VERSION, self.data.dataset_id, CLASSIFIER_VERSION, ENTRANCE_LIMIT_M,
                    snapshot.id if snapshot else policy, variant, z, x, y)
        encoded, hit = self.tiles.compute(repr(identity),
            lambda: encode_tile(self.data, policy, variant, z, x, y, snapshot), len)
        return encoded, hit, (monotonic() - started) * 1000


def tile_template(data: PathData, context: str) -> str:
    return f"/api/paths/tiles/v{render_version(data)}/{data.dataset_id}/{context}/{{variant}}/{{z}}/{{x}}/{{y}}.mvt"

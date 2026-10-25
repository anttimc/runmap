"""Snapshot-scoped geographic distance bands shared by both display algorithms."""

from dataclasses import dataclass
import json
from threading import BoundedSemaphore
from time import monotonic
from typing import Literal

import numpy as np
from rasterio.warp import transform
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from runmap.api.path_tiles import ByteCache, PathTileService, Snapshot, policy_options
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_exploration import NumericExploration, retained_exploration_bytes
from runmap.paths.numeric_database import iter_numeric_payloads
from runmap.paths.numeric_format import decode_numeric_chunk
from runmap.routing.deadlines import check_deadline
from runmap.routing.distance_mesh import (MeshUnavailableError, build_distance_mesh, triangulated_bands)
from runmap.routing.distance_shells import (DistanceBand, DistanceSamples, ShellSettings, build_distance_grid,
    distance_bands, merge_distance_samples, sample_numeric_chunk)
from runmap.routing.numeric_exploration import NumericDistanceData
from runmap.routing.profiles import exploration_profile


ShellMethod = Literal["grid", "triangulation"]


@dataclass(frozen=True)
class ShellPayload:
    encoded: bytes


def projected_coverage(data: SQLitePathData) -> Polygon:
    rings = []
    for ring in data.coverage["geometry"]["coordinates"]:
        longitude, latitude = zip(*ring)
        x, y = transform("EPSG:4326", "EPSG:3067", longitude, latitude)
        rings.append(list(zip(x, y)))
    return Polygon(rings[0], rings[1:])


def polygon_parts(geometry: BaseGeometry) -> tuple[Polygon, ...]:
    if isinstance(geometry, Polygon):
        return (geometry,)
    if hasattr(geometry, "geoms"):
        return tuple(part for item in geometry.geoms for part in polygon_parts(item))
    return ()


def shell_features(bands: tuple[DistanceBand, ...], coverage: Polygon) -> tuple[list[dict[str, object]], int]:
    features: list[dict[str, object]] = []
    vertices = 0
    for band in bands:
        geographic = []
        for rings in band.polygons:
            clipped = make_valid(Polygon(rings[0], rings[1:])).intersection(coverage)
            for polygon in polygon_parts(clipped):
                if polygon.is_empty or polygon.area == 0:
                    continue
                coordinates = []
                for ring in (polygon.exterior, *polygon.interiors):
                    points = np.asarray(ring.coords)
                    lon, lat = transform("EPSG:3067", "EPSG:4326", points[:, 0], points[:, 1])
                    coordinates.append(list(zip(lon, lat)))
                    vertices += len(points)
                geographic.append(coordinates)
        if geographic:
            features.append({"type": "Feature", "properties": {"lower_m": band.lower_m, "upper_m": band.upper_m},
                             "geometry": {"type": "MultiPolygon", "coordinates": geographic}})
    return features, vertices


def load_shell_samples(data: SQLitePathData, snapshot: Snapshot, settings: ShellSettings,
                       deadline: float) -> DistanceSamples:
    numeric = data.numeric
    if numeric is None or not isinstance(snapshot.local, NumericExploration):
        raise ValueError("Distance shells require prepared numeric routing data")
    if not numeric.lock.acquire(timeout=max(0, deadline - monotonic())):
        raise TimeoutError("Distance shell geometry is busy; try again")
    try:
        check_deadline(deadline)
        start = numeric._endpoint(snapshot.distances.snap)
        payloads = []
        for stored in iter_numeric_payloads(numeric.connection, snapshot.local.bounds):
            check_deadline(deadline)
            payloads.append(stored.payload)
    finally:
        numeric.lock.release()
    profile = data.metadata.models.profiles.index(exploration_profile(*policy_options(snapshot.policy)))
    if not isinstance(snapshot.distances, NumericDistanceData):
        raise ValueError("Distance shells require numeric distances")
    samples: list[DistanceSamples] = []
    for payload in payloads:
        check_deadline(deadline)
        samples.append(sample_numeric_chunk(decode_numeric_chunk(payload), snapshot.distances.outward,
            start, profile, snapshot.budget_km * 1000, settings.cell_m / 2, support_m=settings.radius_m * 2))
    result = merge_distance_samples(samples)
    if not len(result.xy):
        raise ValueError("No reachable geometry for distance shells")
    result.xy.setflags(write=False)
    result.distances.setflags(write=False)
    return result


def generate_bands(samples: DistanceSamples, maximum_m: float, settings: ShellSettings, method: ShellMethod,
                    deadline: float) -> tuple[tuple[DistanceBand, ...], ShellMethod]:
    if method == "triangulation":
        try:
            mesh = build_distance_mesh(samples, maximum_m, settings, deadline=deadline)
            return triangulated_bands(mesh, maximum_m, settings, deadline=deadline), method
        except MeshUnavailableError:
            # A single line or isolated start has no area for a triangular surface.
            pass
    grid = build_distance_grid(samples, settings, deadline=deadline)
    return distance_bands(grid, maximum_m, settings), "grid"


class PathShellService:
    """Bound and coalesce geometry sampling and encoded shell work per snapshot."""

    def __init__(self, paths: PathTileService) -> None:
        self.paths = paths
        self.samples: ByteCache[DistanceSamples] = ByteCache(64 * 1024**2, paths.snapshots.ttl)
        self.responses: ByteCache[ShellPayload] = ByteCache(32 * 1024**2, paths.snapshots.ttl)
        self.workers = BoundedSemaphore(2)
        self.coverage = projected_coverage(paths.data) if isinstance(paths.data, SQLitePathData) else None

    def render(self, snapshot: Snapshot, method: ShellMethod, cell_m: float = 50) -> tuple[bytes, bool, float]:
        if method not in {"grid", "triangulation"} or cell_m not in {25, 50, 100}:
            raise ValueError("Unknown shell method or resolution")
        started = monotonic()
        deadline = started + 20
        settings = ShellSettings(cell_m=cell_m)
        key = repr((snapshot.id, method, settings))
        result, hit = self.responses.compute(key, lambda: self._generate(snapshot, settings, method, deadline),
            lambda result: len(result.encoded) + 256, deadline=deadline, allow_oversized=True)
        return result.encoded, hit, (monotonic() - started) * 1000

    def _generate(self, snapshot: Snapshot, settings: ShellSettings, method: ShellMethod,
                   deadline: float) -> ShellPayload:
        if not self.workers.acquire(timeout=max(0, deadline - monotonic())):
            raise TimeoutError("Distance shell generation is busy; try again")
        try:
            return self._encode(snapshot, settings, method, deadline)
        finally:
            self.workers.release()

    def _encode(self, snapshot: Snapshot, settings: ShellSettings, method: ShellMethod,
                 deadline: float) -> ShellPayload:
        started = monotonic()
        data = self.paths.data
        if not isinstance(data, SQLitePathData) or self.coverage is None:
            raise ValueError("Distance shells require prepared numeric routing data")
        samples, sample_hit = self.samples.compute(repr((snapshot.id, settings)),
            lambda: load_shell_samples(data, snapshot, settings, deadline), retained_exploration_bytes,
            deadline=deadline, allow_oversized=True)
        sampled = monotonic()
        bands, used = generate_bands(samples, snapshot.budget_km * 1000, settings, method, deadline)
        contoured = monotonic()
        features, vertices = shell_features(bands, self.coverage)
        check_deadline(deadline)
        result: dict[str, object] = {"type": "FeatureCollection", "features": features,
            "dataset_id": data.dataset_id, "snapshot_id": snapshot.id, "method": method, "method_used": used,
            "cell_m": settings.cell_m, "interval_m": settings.interval_m, "vertices": vertices,
            "sample_cache_hit": sample_hit, "generation_ms": (monotonic() - started) * 1000,
            "sampling_ms": (sampled - started) * 1000, "surface_ms": (contoured - sampled) * 1000}
        return ShellPayload(json.dumps(result, separators=(",", ":"), allow_nan=False).encode())

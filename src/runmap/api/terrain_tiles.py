"""Versioned XYZ terrain rendering backed by haloed native-grid analysis tiles."""

from collections import OrderedDict
import math
import struct
from threading import Event, RLock
from time import perf_counter

import contourpy
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform, transform_bounds

from runmap.api.terrain import colorize_slope_png, slope_legend
from runmap.elevation.store import DERIVED_NODATA, MAGNITUDE_SCALE
from runmap.regions import ANALYSIS_CRS, SOURCE_RESOLUTION_M


WEB_MERCATOR_LIMIT = 20037508.342789244
RENDER_VERSION = 5
CONTOUR_RENDER_VERSION = 2
CONTOUR_INTERVAL_M = 5
CONTOUR_INDEX_INTERVAL_M = 25
CONTOUR_EXTENT = 4096
CONTOUR_SAMPLE_SCALE = 2
HOSTED_WAVELENGTHS_M = (0, 20, 40, 60, 80, 100)
DISPLAY_MIN_ZOOM = 8
DISPLAY_MAX_ZOOM = 15
DISPLAY_TILE_SIZE = 256
CONTOUR_BUFFER_PX = 2


def xyz_bounds(z, x, y):
    count = 1 << z
    if z < 0 or not (0 <= x < count and 0 <= y < count):
        raise ValueError("Invalid XYZ tile coordinate")
    span = 2 * WEB_MERCATOR_LIMIT / count
    left = -WEB_MERCATOR_LIMIT + x * span
    right = left + span
    top = WEB_MERCATOR_LIMIT - y * span
    bottom = top - span
    return left, bottom, right, top


class _PendingRender:
    """Share one in-flight result with requests for the same cache key."""

    def __init__(self):
        self.event = Event()
        self.result = None
        self.error = None


class TerrainTileService:
    def __init__(self, region, store, cache_entries=128):
        self.region = region
        self.store = store
        self.manifest_id = store.active_manifest_for_region(region)
        self._cache_entries = cache_entries
        self._cache = OrderedDict()
        self._lock = RLock()
        self._inflight = {}
        # ElevationStore writes one deterministic path per derived core. Keep
        # lookup/generation serialized until missing presets move to a bounded
        # background worker; display reprojection happens outside this lock.
        self._derived_lock = RLock()
        west, south, east, north = region.bounds
        self.bounds_wgs84 = transform_bounds(
            ANALYSIS_CRS, "EPSG:4326", west, south, east, north, densify_pts=21
        )
        longitude, latitude = transform(
            ANALYSIS_CRS,
            "EPSG:4326",
            [west, east, east, west],
            [north, north, south, south],
        )
        self.image_coordinates = tuple(zip(longitude, latitude))

    def config(self):
        return {
            "bounds_wgs84": list(self.bounds_wgs84),
            "image_coordinates": [list(coordinate) for coordinate in self.image_coordinates],
            "source_shape": list(self.region.shape),
            "resolution_m": float(SOURCE_RESOLUTION_M),
            "smoothing": {
                "name": "spectral biharmonic thin plate",
                "parameter": "half_amplitude_wavelength_m",
                "minimum": min(HOSTED_WAVELENGTHS_M),
                "maximum": max(HOSTED_WAVELENGTHS_M),
                "step": 20,
                "default": 40,
                "raw_value": 0,
                "values": list(HOSTED_WAVELENGTHS_M),
            },
            "slope_legend": slope_legend(),
            "terrain_tiles": {
                "template": (
                    f"/api/regions/{self.region.id}/terrain/v{RENDER_VERSION}"
                    f"/{self.manifest_id}/w{{wavelength}}/{{z}}/{{x}}/{{y}}.png"
                ),
                "tile_size": DISPLAY_TILE_SIZE,
                "min_zoom": DISPLAY_MIN_ZOOM,
                "max_zoom": DISPLAY_MAX_ZOOM,
            },
            "contour_tiles": {
                "template": (
                    f"/api/regions/{self.region.id}/contours/v{CONTOUR_RENDER_VERSION}"
                    f"/{self.manifest_id}/{{z}}/{{x}}/{{y}}.mvt"
                ),
                "tile_size": DISPLAY_TILE_SIZE,
                "min_zoom": 12,
                "max_zoom": DISPLAY_MAX_ZOOM,
                "interval_m": CONTOUR_INTERVAL_M,
                "index_interval_m": CONTOUR_INDEX_INTERVAL_M,
            },
            "region": {
                "id": self.region.id,
                "title": self.region.title,
                "bounds_epsg3067": list(self.region.bounds),
                "initial_zoom": self.region.initial_zoom,
                "manifest_id": self.manifest_id,
            },
        }

    def render_tile(self, wavelength_m, z, x, y):
        wavelength = int(wavelength_m)
        if wavelength not in HOSTED_WAVELENGTHS_M or wavelength != wavelength_m:
            raise ValueError(f"Wavelength must be one of {HOSTED_WAVELENGTHS_M}")
        if not DISPLAY_MIN_ZOOM <= z <= DISPLAY_MAX_ZOOM:
            raise ValueError("Zoom is outside the terrain tile range")
        key = "terrain", wavelength, z, x, y
        return self._render_cached(
            key, lambda: self._render_uncached(wavelength, z, x, y)
        )

    def render_contour_tile(self, z, x, y):
        """Render fixed source-elevation contours as a transparent PNG tile."""
        if not 12 <= z <= DISPLAY_MAX_ZOOM:
            raise ValueError("Zoom is outside the contour tile range")
        key = "contours", z, x, y
        return self._render_cached(
            key, lambda: self._render_contours_uncached(z, x, y)
        )

    def _render_cached(self, key, render):
        """Return cached bytes or compute one key without blocking other keys."""
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached, 0.0
            pending = self._inflight.get(key)
            if pending is None:
                pending = _PendingRender()
                self._inflight[key] = pending
                owner = True
            else:
                owner = False

        if not owner:
            pending.event.wait()
            if pending.error is not None:
                raise pending.error
            return pending.result, 0.0

        try:
            started = perf_counter()
            image = render()
            elapsed = perf_counter() - started
        except BaseException as exc:
            with self._lock:
                pending.error = exc
                self._inflight.pop(key, None)
                pending.event.set()
            raise

        with self._lock:
            self._cache[key] = image
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_entries:
                self._cache.popitem(last=False)
            pending.result = image
            self._inflight.pop(key, None)
            pending.event.set()
            return image, elapsed

    def _render_contours_uncached(self, z, x, y):
        mercator_bounds = xyz_bounds(z, x, y)
        pixel_size = (mercator_bounds[2] - mercator_bounds[0]) / DISPLAY_TILE_SIZE
        buffered_bounds = (
            mercator_bounds[0] - CONTOUR_BUFFER_PX * pixel_size,
            mercator_bounds[1] - CONTOUR_BUFFER_PX * pixel_size,
            mercator_bounds[2] + CONTOUR_BUFFER_PX * pixel_size,
            mercator_bounds[3] + CONTOUR_BUFFER_PX * pixel_size,
        )
        request_bounds = transform_bounds(
            "EPSG:3857", ANALYSIS_CRS, *buffered_bounds, densify_pts=21
        )
        region_west, region_south, region_east, region_north = self.region.bounds
        if (
            request_bounds[2] <= region_west or request_bounds[0] >= region_east
            or request_bounds[3] <= region_south or request_bounds[1] >= region_north
        ):
            return _encode_mvt([])

        # ElevationStore reads native-grid-aligned rectangles. Clip and expand
        # the requested rectangle to the 2 m grid before reprojection.
        west = max(
            region_west,
            math.floor(request_bounds[0] / SOURCE_RESOLUTION_M) * SOURCE_RESOLUTION_M,
        )
        south = max(
            region_south,
            math.floor(request_bounds[1] / SOURCE_RESOLUTION_M) * SOURCE_RESOLUTION_M,
        )
        east = min(
            region_east,
            math.ceil(request_bounds[2] / SOURCE_RESOLUTION_M) * SOURCE_RESOLUTION_M,
        )
        north = min(
            region_north,
            math.ceil(request_bounds[3] / SOURCE_RESOLUTION_M) * SOURCE_RESOLUTION_M,
        )
        elevation, source_transform = self.store.read_bounds(
            self.manifest_id, (west, south, east, north)
        )
        buffered_size = (
            DISPLAY_TILE_SIZE + 2 * CONTOUR_BUFFER_PX
        ) * CONTOUR_SAMPLE_SCALE
        destination = np.full((buffered_size, buffered_size), np.nan, dtype=np.float32)
        reproject(
            source=elevation,
            destination=destination,
            src_transform=source_transform,
            src_crs=ANALYSIS_CRS,
            src_nodata=np.nan,
            dst_transform=from_bounds(*buffered_bounds, buffered_size, buffered_size),
            dst_crs="EPSG:3857",
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        return _encode_contour_mvt(destination)

    def _render_uncached(self, wavelength, z, x, y):
        mercator_bounds = xyz_bounds(z, x, y)
        request_bounds = transform_bounds(
            "EPSG:3857", ANALYSIS_CRS, *mercator_bounds, densify_pts=21
        )
        region_west, region_south, region_east, region_north = self.region.bounds
        if (
            request_bounds[2] <= region_west or request_bounds[0] >= region_east
            or request_bounds[3] <= region_south or request_bounds[1] >= region_north
        ):
            return colorize_slope_png(
                np.full((DISPLAY_TILE_SIZE, DISPLAY_TILE_SIZE), np.nan, dtype=np.float32)
            )
        tiles = [
            tile for tile in self.region.analysis_tiles()
            if tile.east > request_bounds[0] and tile.west < request_bounds[2]
            and tile.north > request_bounds[1] and tile.south < request_bounds[3]
        ]
        with self._derived_lock:
            paths = [
                self.store.ensure_derived_tile(self.manifest_id, tile, wavelength)
                for tile in tiles
            ]
        destination = np.full(
            (DISPLAY_TILE_SIZE, DISPLAY_TILE_SIZE), np.nan, dtype=np.float32
        )
        for path in paths:
            with rasterio.open(path) as dataset:
                warped = np.full(
                    (DISPLAY_TILE_SIZE, DISPLAY_TILE_SIZE), np.nan, dtype=np.float32
                )
                reproject(
                    source=rasterio.band(dataset, 1),
                    destination=warped,
                    src_nodata=DERIVED_NODATA,
                    dst_transform=from_bounds(
                        *mercator_bounds, DISPLAY_TILE_SIZE, DISPLAY_TILE_SIZE
                    ),
                    dst_crs="EPSG:3857",
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear,
                )
            covered = np.isfinite(warped)
            destination[covered] = warped[covered]
        return colorize_slope_png(100 * destination * MAGNITUDE_SCALE)


def _varint(value):
    encoded = bytearray()
    while value > 0x7f:
        encoded.append((value & 0x7f) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _field(number, wire_type, value):
    key = _varint((number << 3) | wire_type)
    if wire_type == 2:
        return key + _varint(len(value)) + value
    return key + _varint(value)


def _zigzag(value):
    return (value << 1) ^ (value >> 63)


def _line_geometry(points):
    rounded = np.rint(points).astype(np.int64)
    if len(rounded) < 2:
        return b""
    # Consecutive duplicate coordinates produce zero-length vector commands.
    keep = np.ones(len(rounded), dtype=bool)
    keep[1:] = np.any(rounded[1:] != rounded[:-1], axis=1)
    rounded = rounded[keep]
    if len(rounded) < 2:
        return b""
    commands = bytearray(_varint(9))  # MoveTo, one point.
    x = y = 0
    first_x, first_y = rounded[0]
    commands.extend(_varint(_zigzag(int(first_x) - x)))
    commands.extend(_varint(_zigzag(int(first_y) - y)))
    x, y = int(first_x), int(first_y)
    commands.extend(_varint(((len(rounded) - 1) << 3) | 2))  # LineTo.
    for next_x, next_y in rounded[1:]:
        next_x, next_y = int(next_x), int(next_y)
        commands.extend(_varint(_zigzag(next_x - x)))
        commands.extend(_varint(_zigzag(next_y - y)))
        x, y = next_x, next_y
    return bytes(commands)


def _encode_mvt(features):
    keys = ("elevation", "index")
    elevations = sorted({feature[0] for feature in features})
    values = [("int", elevation) for elevation in elevations] + [
        ("bool", False),
        ("bool", True),
    ]
    value_indices = {value: index for index, value in enumerate(values)}
    layer = bytearray(_field(1, 2, b"contours"))
    for feature_id, (elevation, points) in enumerate(features, 1):
        geometry = _line_geometry(points)
        if not geometry:
            continue
        index = elevation % CONTOUR_INDEX_INTERVAL_M == 0
        tags = b"".join(
            _varint(item)
            for item in (
                0, value_indices[("int", elevation)],
                1, value_indices[("bool", index)],
            )
        )
        feature = (
            _field(1, 0, feature_id)
            + _field(2, 2, tags)
            + _field(3, 0, 2)  # GeomType.LINESTRING
            + _field(4, 2, geometry)
        )
        layer.extend(_field(2, 2, feature))
    for key in keys:
        layer.extend(_field(3, 2, key.encode()))
    for kind, value in values:
        # Protobuf int64 uses a two's-complement varint for negative elevations.
        encoded = _field(4, 0, value & ((1 << 64) - 1)) if kind == "int" else _field(7, 0, int(value))
        layer.extend(_field(4, 2, encoded))
    layer.extend(_field(5, 0, CONTOUR_EXTENT))
    layer.extend(_field(15, 0, 2))
    return _field(3, 2, bytes(layer))


def _encode_contour_mvt(elevation):
    finite = elevation[np.isfinite(elevation)]
    if not finite.size:
        return _encode_mvt([])
    generator = contourpy.contour_generator(z=elevation, line_type="Separate")
    first = math.ceil(float(finite.min()) / CONTOUR_INTERVAL_M) * CONTOUR_INTERVAL_M
    last = math.floor(float(finite.max()) / CONTOUR_INTERVAL_M) * CONTOUR_INTERVAL_M
    units_per_sample = CONTOUR_EXTENT / (DISPLAY_TILE_SIZE * CONTOUR_SAMPLE_SCALE)
    buffer_samples = CONTOUR_BUFFER_PX * CONTOUR_SAMPLE_SCALE
    offset = buffer_samples * units_per_sample
    features = []
    for level in range(first, last + 1, CONTOUR_INTERVAL_M):
        for line in generator.lines(level):
            points = line * units_per_sample - offset
            features.append((level, points))
    return _encode_mvt(features)

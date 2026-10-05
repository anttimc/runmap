"""Versioned XYZ terrain rendering backed by haloed native-grid analysis tiles."""

from collections import OrderedDict
from threading import RLock
from time import perf_counter

import numpy as np
import rasterio
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import Resampling, reproject, transform, transform_bounds

from runmap.api.terrain import colorize_slope_png, slope_legend
from runmap.elevation.store import DERIVED_NODATA, MAGNITUDE_SCALE
from runmap.regions import ANALYSIS_CRS, SOURCE_RESOLUTION_M


WEB_MERCATOR_LIMIT = 20037508.342789244
RENDER_VERSION = 4
HOSTED_WAVELENGTHS_M = (0, 20, 40, 60, 80, 100)
DISPLAY_MIN_ZOOM = 8
DISPLAY_MAX_ZOOM = 15
DISPLAY_TILE_SIZE = 256


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


class TerrainTileService:
    def __init__(self, region, store, cache_entries=128):
        self.region = region
        self.store = store
        self.manifest_id = store.active_manifest_for_region(region)
        self._cache_entries = cache_entries
        self._cache = OrderedDict()
        self._lock = RLock()
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
                    "/w{wavelength}/{z}/{x}/{y}.png"
                ),
                "tile_size": DISPLAY_TILE_SIZE,
                "min_zoom": DISPLAY_MIN_ZOOM,
                "max_zoom": DISPLAY_MAX_ZOOM,
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
        key = wavelength, z, x, y
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached, 0.0
            started = perf_counter()
            image = self._render_uncached(wavelength, z, x, y)
            elapsed = perf_counter() - started
            self._cache[key] = image
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_entries:
                self._cache.popitem(last=False)
            return image, elapsed

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
        paths = [self.store.ensure_derived_tile(self.manifest_id, tile, wavelength) for tile in tiles]
        west = min(tile.west for tile in tiles)
        south = min(tile.south for tile in tiles)
        east = max(tile.east for tile in tiles)
        north = max(tile.north for tile in tiles)
        rows = int((north - south) / SOURCE_RESOLUTION_M)
        cols = int((east - west) / SOURCE_RESOLUTION_M)
        source = np.full((rows, cols), np.nan, dtype=np.float32)
        for tile, path in zip(tiles, paths):
            with rasterio.open(path) as dataset:
                encoded = dataset.read(1)
            values = np.where(
                encoded == DERIVED_NODATA, np.nan, encoded * MAGNITUDE_SCALE
            ).astype(np.float32)
            row = int((north - tile.north) / SOURCE_RESOLUTION_M)
            col = int((tile.west - west) / SOURCE_RESOLUTION_M)
            source[row:row + values.shape[0], col:col + values.shape[1]] = values
        destination = np.full(
            (DISPLAY_TILE_SIZE, DISPLAY_TILE_SIZE), np.nan, dtype=np.float32
        )
        reproject(
            source=source,
            destination=destination,
            src_transform=from_origin(west, north, SOURCE_RESOLUTION_M, SOURCE_RESOLUTION_M),
            src_crs=ANALYSIS_CRS,
            src_nodata=np.nan,
            dst_transform=from_bounds(*mercator_bounds, DISPLAY_TILE_SIZE, DISPLAY_TILE_SIZE),
            dst_crs="EPSG:3857",
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        return colorize_slope_png(100 * destination)

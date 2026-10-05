"""Prepared terrain rendering for the interactive map."""

from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from threading import RLock
from time import perf_counter

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import array_bounds
from rasterio.warp import transform, transform_bounds

from runmap.terrain.derivatives import terrain_gradient
from runmap.terrain.smoothing import PreparedThinPlateSmoother


SLOPE_STOPS_PERCENT = np.array([0, 2, 5, 10, 20, 40], dtype=np.float64)
SLOPE_COLORS = np.array([
    [17, 24, 39],
    [24, 61, 89],
    [8, 127, 140],
    [244, 211, 94],
    [238, 150, 75],
    [209, 73, 91],
], dtype=np.uint8)
# Retain the existing piecewise-linear colour ramp, but use a deliberately
# small number of samples from it in the display PNG.  This gives the terrain
# a banded, contour-map appearance without quantising the analysis raster.
SLOPE_COLOR_BANDS = 6
SLOPE_OPAQUE_AT_PERCENT = 15.0


def slope_palette():
    """Return the display palette sampled from the shared continuous ramp."""
    values = np.linspace(0, SLOPE_STOPS_PERCENT[-1], SLOPE_COLOR_BANDS)
    colors = np.stack([
        np.interp(values, SLOPE_STOPS_PERCENT, SLOPE_COLORS[:, channel])
        for channel in range(3)
    ], axis=1).round().astype(np.uint8)
    return values, colors


def slope_opacity(slope_percent):
    """Ease from transparent at 0% slope to opaque at 15% slope."""
    ratio = np.clip(np.asarray(slope_percent) / SLOPE_OPAQUE_AT_PERCENT, 0, 1)
    return ratio * ratio * (3 - 2 * ratio)


def slope_legend():
    """Return the discrete palette advertised to the map UI."""
    values, colors = slope_palette()
    opacities = slope_opacity(values)
    return [
        {
            "percent": float(value),
            "color": "#" + "".join(f"{channel:02x}" for channel in color),
            "opacity": float(opacity),
        }
        for value, color, opacity in zip(values, colors, opacities)
    ]


def colorize_slope_png(slope_percent):
    """Encode slopes using sampled ramp colours and a transparent nodata entry."""
    palette = np.zeros((256, 3), dtype=np.uint8)
    palette_values = np.linspace(0, SLOPE_STOPS_PERCENT[-1], 255)
    _, colors = slope_palette()
    color_bins = np.minimum(
        (palette_values * SLOPE_COLOR_BANDS / SLOPE_STOPS_PERCENT[-1]).astype(np.intp),
        SLOPE_COLOR_BANDS - 1,
    )
    palette[1:] = colors[color_bins]
    finite = np.isfinite(slope_percent)
    indices = np.zeros(slope_percent.shape, dtype=np.uint8)
    clipped = np.clip(slope_percent[finite], 0, SLOPE_STOPS_PERCENT[-1])
    indices[finite] = 1 + np.rint(
        254 * clipped / SLOPE_STOPS_PERCENT[-1]
    ).astype(np.uint8)
    transparency = np.zeros(256, dtype=np.uint8)
    transparency[1:] = np.rint(255 * slope_opacity(palette_values)).astype(np.uint8)
    output = BytesIO()
    image = Image.fromarray(indices)
    image.putpalette(palette.ravel().tolist())
    image.save(output, format="PNG", compress_level=4, transparency=bytes(transparency))
    return output.getvalue()


class TerrainRenderer:
    """Keep source spectral data in memory and render bounded PNG overlays."""

    def __init__(
        self,
        elevation,
        resolution_m,
        bounds_wgs84,
        image_coordinates=None,
        cache_entries=12,
        metadata=None,
    ):
        z = np.asarray(elevation, dtype=np.float64)
        if z.ndim != 2 or not np.isfinite(z).all():
            raise ValueError("Interactive smoothing requires a complete finite 2D raster")
        self._smoother = PreparedThinPlateSmoother(z, resolution_m)
        self._shape = z.shape
        self.resolution_m = float(resolution_m)
        self.bounds_wgs84 = tuple(float(value) for value in bounds_wgs84)
        if image_coordinates is None:
            west, south, east, north = self.bounds_wgs84
            image_coordinates = (
                (west, north),
                (east, north),
                (east, south),
                (west, south),
            )
        coordinates = np.asarray(image_coordinates, dtype=np.float64)
        if coordinates.shape != (4, 2) or not np.isfinite(coordinates).all():
            raise ValueError("Image coordinates must contain four finite lon/lat pairs")
        self.image_coordinates = tuple(
            (float(longitude), float(latitude)) for longitude, latitude in coordinates
        )
        self._cache_entries = cache_entries
        self._cache = OrderedDict()
        self._lock = RLock()
        self.metadata = dict(metadata or {})

    @classmethod
    def from_raster(cls, path):
        path = Path(path)
        with rasterio.open(path) as dataset:
            if dataset.count != 1:
                raise ValueError("Expected one elevation band")
            if not np.allclose(dataset.res, (dataset.res[0], dataset.res[0])):
                raise ValueError("Interactive smoothing requires square raster cells")
            elevation = dataset.read(1, masked=True).filled(np.nan)
            bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds, densify_pts=21)
            raster_bounds = dataset.bounds
            corner_x = [
                raster_bounds.left,
                raster_bounds.right,
                raster_bounds.right,
                raster_bounds.left,
            ]
            corner_y = [
                raster_bounds.top,
                raster_bounds.top,
                raster_bounds.bottom,
                raster_bounds.bottom,
            ]
            longitude, latitude = transform(
                dataset.crs, "EPSG:4326", corner_x, corner_y
            )
            image_coordinates = tuple(zip(longitude, latitude))
            resolution = float(dataset.res[0])
        return cls(elevation, resolution, bounds, image_coordinates=image_coordinates)

    @classmethod
    def from_grid(cls, elevation, transform_affine, crs, metadata=None):
        """Create a renderer from a north-up in-memory metric raster grid."""
        rows, cols = np.asarray(elevation).shape
        west, south, east, north = array_bounds(rows, cols, transform_affine)
        bounds = transform_bounds(crs, "EPSG:4326", west, south, east, north, densify_pts=21)
        longitude, latitude = transform(
            crs,
            "EPSG:4326",
            [west, east, east, west],
            [north, north, south, south],
        )
        return cls(
            elevation,
            float(transform_affine.a),
            bounds,
            image_coordinates=tuple(zip(longitude, latitude)),
            metadata=metadata,
        )

    @property
    def shape(self):
        return self._shape

    def config(self):
        result = {
            "bounds_wgs84": list(self.bounds_wgs84),
            "image_coordinates": [list(coordinate) for coordinate in self.image_coordinates],
            "source_shape": list(self._shape),
            "resolution_m": self.resolution_m,
            "smoothing": {
                "name": "spectral biharmonic thin plate",
                "parameter": "half_amplitude_wavelength_m",
                "minimum": 0,
                "maximum": 100,
                "step": 2,
                "default": 40,
                "raw_value": 0,
            },
            "slope_legend": slope_legend(),
        }
        result.update(self.metadata)
        return result

    def render_png(self, wavelength_m, max_size=1600):
        wavelength = round(float(wavelength_m), 2)
        max_size = int(max_size)
        if not np.isfinite(wavelength) or not 0 <= wavelength <= 100:
            raise ValueError("Wavelength must be between 0 and 100 metres")
        if not 256 <= max_size <= 2048:
            raise ValueError("Maximum image size must be between 256 and 2048 pixels")
        key = (wavelength, max_size)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached, 0.0
            started = perf_counter()
            surface = self._smoother.smooth(wavelength)
            _, _, magnitude = terrain_gradient(surface, self.resolution_m, self.resolution_m)
            slope_percent = self._resize(100 * magnitude, max_size)
            image_bytes = self._colorize(slope_percent)
            elapsed = perf_counter() - started
            self._cache[key] = image_bytes
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_entries:
                self._cache.popitem(last=False)
            return image_bytes, elapsed

    @staticmethod
    def _resize(values, max_size):
        height, width = values.shape
        scale = min(1.0, max_size / max(height, width))
        target = (max(1, round(width * scale)), max(1, round(height * scale)))
        if target == (width, height):
            return values
        return np.asarray(
            Image.fromarray(values.astype(np.float32)).resize(
                target, resample=Image.Resampling.BILINEAR
            ),
            dtype=np.float64,
        )

    @staticmethod
    def _colorize(slope_percent):
        return colorize_slope_png(slope_percent)

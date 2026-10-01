"""Metric-aware derivatives for north-up elevation grids."""

import numpy as np


def terrain_gradient(elevation, resolution_x, resolution_y, valid=None):
    """Return east/north components and magnitude in metres per metre.

    NumPy axis 0 follows raster rows southward, hence the sign inversion for
    the north component. One-sided differences are used at array edges.
    """
    z = np.asarray(elevation, dtype=np.float64)
    if z.ndim != 2 or resolution_x <= 0 or resolution_y <= 0:
        raise ValueError("Expected a 2D surface and positive resolutions")
    south, east = np.gradient(z, resolution_y, resolution_x)
    north = -south
    magnitude = np.hypot(east, north)
    if valid is not None:
        valid = np.asarray(valid, dtype=bool)
        if valid.shape != z.shape:
            raise ValueError("Mask and elevation shapes differ")
        east = np.where(valid, east, np.nan)
        north = np.where(valid, north, np.nan)
        magnitude = np.where(valid, magnitude, np.nan)
    return east, north, magnitude


def uphill_bearing_degrees(east, north, minimum_magnitude=0.0):
    """Clockwise bearing from grid north; NaN where direction is unreliable."""
    east, north = np.asarray(east), np.asarray(north)
    magnitude = np.hypot(east, north)
    bearing = np.mod(np.degrees(np.arctan2(east, north)), 360.0)
    return np.where(magnitude > minimum_magnitude, bearing, np.nan)

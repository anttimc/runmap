"""Filtered Delaunay distance surfaces and filled triangle bands."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.spatial import Delaunay, QhullError
from shapely import coverage_union_all, polygons, set_precision, union_all
from shapely.geometry import Polygon, MultiPolygon
from shapely.errors import GEOSException

from runmap.routing.distance_shells import DistanceBand, DistanceSamples, FloatArray, IntArray, ShellSettings, validate_shell_settings
from runmap.routing.deadlines import check_deadline


class MeshUnavailableError(ValueError):
    pass


@dataclass(frozen=True)
class DistanceMesh:
    points: FloatArray
    values: FloatArray
    triangles: IntArray


def mesh_sites(samples: DistanceSamples, cell_m: float | None, *, interval_m: float = 500,
               boundary_window_m: float | None = None, maximum_m: float = 20000) -> DistanceSamples:
    """Merge duplicate sites, optionally retaining one representative per cell."""
    if not len(samples.xy):
        return samples
    if boundary_window_m is not None:
        if cell_m is None or not 0 < boundary_window_m < interval_m / 2:
            raise ValueError("Boundary reduction requires cells and a window below half the band interval")
        remainder = samples.distances % interval_m
        near = np.minimum(remainder, interval_m - remainder) <= boundary_window_m
        near |= np.abs(samples.distances - maximum_m) <= boundary_window_m
        spacing = np.where(near, cell_m, cell_m * 4)
        keys = np.floor(samples.xy / spacing[:, None]).astype(np.int64)
    else:
        keys = samples.xy if cell_m is None else np.floor(samples.xy / cell_m).astype(np.int64)
    if cell_m is None:
        _, inverse = np.unique(keys, axis=0, return_inverse=True)
    else:
        shifted = keys - keys.min(axis=0)
        packed = shifted[:, 0] * (shifted[:, 1].max() + 1) + shifted[:, 1]
        if boundary_window_m is not None:
            packed = packed * 2 + near
        _, inverse = np.unique(packed, return_inverse=True)
    indices = _extreme_indices(samples.distances, inverse, minimum=True)
    if boundary_window_m is not None:
        # Retain both distance extremes away from boundaries as coarse sign anchors.
        additional = _extreme_indices(samples.distances, inverse, minimum=False)
        indices = np.unique(np.r_[indices, additional[~near[additional]]])
    return DistanceSamples(samples.xy[indices], samples.distances[indices])


def _extreme_indices(values: NDArray[np.float64], groups: NDArray[np.int64], *, minimum: bool
                     ) -> NDArray[np.int64]:
    count = int(groups.max()) + 1
    extremes = np.full(count, np.inf if minimum else -np.inf)
    if minimum:
        np.minimum.at(extremes, groups, values)
    else:
        np.maximum.at(extremes, groups, values)
    candidates = np.flatnonzero(values == extremes[groups])
    indices = np.full(count, len(values), dtype=np.int64)
    np.minimum.at(indices, groups[candidates], candidates)
    return indices


def build_distance_mesh(samples: DistanceSamples, maximum_m: float, settings: ShellSettings,
                        *, deadline: float | None = None) -> DistanceMesh:
    validate_shell_settings(settings)
    check_deadline(deadline)
    sites = mesh_sites(samples, settings.cell_m, interval_m=settings.interval_m,
                       boundary_window_m=min(50, settings.interval_m / 4), maximum_m=maximum_m)
    if len(sites.xy) < 3:
        raise MeshUnavailableError("Too few distinct sites for triangulation")
    if len(sites.xy) > 400_000:
        raise ValueError("Distance mesh exceeds the site budget; try a smaller distance")
    try:
        triangulation = Delaunay(sites.xy)
    except QhullError as exc:
        raise MeshUnavailableError("Reachable geometry has no two-dimensional mesh") from exc
    check_deadline(deadline)
    points = sites.xy[triangulation.simplices]
    sides = np.linalg.norm(points - np.roll(points, 1, axis=1), axis=2)
    keep = sides.max(axis=1) <= settings.radius_m * 2
    triangles = triangulation.simplices[keep].astype(np.int64)
    if not len(triangles):
        raise MeshUnavailableError("No triangles within the interpolation radius")
    edges = points[keep, 1:] - points[keep, :1]
    area = np.abs(edges[:, 0, 0] * edges[:, 1, 1] - edges[:, 0, 1] * edges[:, 1, 0]).sum() / 2
    if area < settings.cell_m**2:
        raise MeshUnavailableError("Reachable geometry is too narrow for an area mesh")
    return DistanceMesh(sites.xy, sites.distances, triangles)


def _clip_level(points: FloatArray, values: FloatArray, counts: IntArray, level: float,
                *, above: bool) -> tuple[FloatArray, FloatArray, IntArray]:
    """Clip convex triangle fragments, preserving identical shared edge crossings."""
    rows = np.arange(len(points))[:, None]
    edges = np.arange(points.shape[1])[None, :]
    valid = edges < counts[:, None]
    following = (edges + 1) % np.maximum(counts[:, None], 1)
    next_points, next_values = points[rows, following], values[rows, following]
    inside = values >= level if above else values <= level
    next_inside = next_values >= level if above else next_values <= level
    crosses = valid & (inside != next_inside)
    # Order endpoints by scalar value so neighboring triangles round identically.
    ascending = values <= next_values
    low = np.where(ascending, values, next_values)
    high = np.where(ascending, next_values, values)
    first = np.where(ascending[..., None], points, next_points)
    second = np.where(ascending[..., None], next_points, points)
    fraction = np.divide(level - low, high - low, out=np.zeros_like(values), where=crosses)
    intersections = first + fraction[..., None] * (second - first)
    emit = np.stack((valid & inside, crosses), axis=2).reshape(len(points), -1)
    candidates = np.stack((points, intersections), axis=2).reshape(len(points), -1, 2)
    scalars = np.stack((values, np.full_like(values, level)), axis=2).reshape(len(points), -1)
    counts_out = emit.sum(axis=1).astype(np.int64)
    slots = np.cumsum(emit, axis=1) - 1
    row, column = np.nonzero(emit)
    output = np.zeros((len(points), int(counts_out.max(initial=0)), 2))
    output_values = np.zeros(output.shape[:2])
    output[row, slots[row, column]] = candidates[row, column]
    output_values[row, slots[row, column]] = scalars[row, column]
    return output, output_values, counts_out


def _band_polygons(mesh: DistanceMesh, lower: float, upper: float) -> tuple[tuple[FloatArray, ...], ...]:
    values = mesh.values[mesh.triangles]
    above = values.max(axis=1) > lower if lower > 0 else values.max(axis=1) >= lower
    selected = (values.min(axis=1) <= upper) & above
    points = mesh.points[mesh.triangles[selected]]
    values = values[selected]
    if not len(points):
        return ()
    counts = np.full(len(points), 3, dtype=np.int64)
    points, values, counts = _clip_level(points, values, counts, lower, above=True)
    points, _, counts = _clip_level(points, values, counts, upper, above=False)
    parts = [polygons(points[counts == count, :count]) for count in np.unique(counts) if count >= 3]
    if not parts:
        return ()
    # Millimetre precision removes floating slivers well below the display resolution.
    fragments = set_precision(np.concatenate(parts), .001)
    try:
        merged = coverage_union_all(fragments)
        if not merged.is_valid:
            merged = union_all(fragments)
    except GEOSException:
        merged = union_all(fragments)
    components = (merged,) if isinstance(merged, Polygon) else merged.geoms if isinstance(merged, MultiPolygon) else ()
    return tuple((np.asarray(polygon.exterior.coords),
                  *(np.asarray(ring.coords) for ring in polygon.interiors))
                 for polygon in components if not polygon.is_empty and polygon.area > 0)


def triangulated_bands(mesh: DistanceMesh, maximum_m: float, settings: ShellSettings,
                       *, deadline: float | None = None) -> tuple[DistanceBand, ...]:
    levels = np.r_[np.arange(0., maximum_m, settings.interval_m), maximum_m]
    bands: list[DistanceBand] = []
    for lower, upper in zip(levels[:-1], levels[1:]):
        check_deadline(deadline)
        bands.append(DistanceBand(float(lower), float(upper), _band_polygons(mesh, float(lower), float(upper))))
    return tuple(bands)

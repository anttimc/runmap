"""Geographic distance bands from sparse network search results."""

from dataclasses import dataclass
import math
from typing import cast

import contourpy
import numpy as np
from numpy.typing import NDArray
from scipy.spatial import cKDTree
from runmap.routing.deadlines import check_deadline

from runmap.paths.numeric_format import NumericChunk
from runmap.routing.numeric_search import NumericDistances, NumericEndpoint


FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


@dataclass(frozen=True)
class ShellSettings:
    cell_m: float = 50
    radius_m: float = 150
    interval_m: float = 500
    max_cells: int = 4_000_000


@dataclass(frozen=True)
class DistanceSamples:
    xy: FloatArray
    distances: FloatArray


@dataclass(frozen=True)
class DistanceGrid:
    x: FloatArray
    y: FloatArray
    values: FloatArray


@dataclass(frozen=True)
class DistanceBand:
    lower_m: float
    upper_m: float
    # Each polygon contains its exterior followed by any interior rings.
    polygons: tuple[tuple[FloatArray, ...], ...]


def validate_shell_settings(settings: ShellSettings) -> None:
    if not all(math.isfinite(v) and v > 0 for v in
               (settings.cell_m, settings.radius_m, settings.interval_m)) or settings.max_cells < 4:
        raise ValueError("Shell dimensions must be finite and positive, with at least four cells")


def _node_values(nodes: IntArray, distances: NumericDistances) -> FloatArray:
    # Native search appends the virtual start after sorted real node IDs.
    ids, values = distances.node_ids, distances.values
    if len(ids) and ids[-1] < 0:
        ids, values = ids[:-1], values[:-1]
    result = np.full(len(nodes), np.inf)
    if len(ids):
        positions = np.searchsorted(ids, nodes)
        safe = np.minimum(positions, len(ids) - 1)
        found = (positions < len(ids)) & (ids[safe] == nodes)
        result[found] = values[safe[found]]
    return result


def _segment_anchors(chunk: NumericChunk, distances: NumericDistances, profile: int
                     ) -> tuple[FloatArray, FloatArray, FloatArray, NDArray[np.bool_]]:
    a = chunk.arrays
    segments = cast(IntArray, a["arc_segments"])
    nodes = cast(IntArray, a["arc_nodes"])
    permitted = a["arc_allowed"][:, profile] != 0
    backwards = a["arc_reverse"] != 0
    bases = _node_values(cast(IntArray, a["node_ids"]), distances)[nodes[:, 0]]
    forward, backward = np.full(chunk.counts.segments, np.inf), np.full(chunk.counts.segments, np.inf)
    lengths = np.zeros(chunk.counts.segments)
    directions = np.zeros((chunk.counts.segments, 2), dtype=np.bool_)
    np.maximum.at(lengths, segments, a["arc_lengths"])
    for reverse, output in ((False, forward), (True, backward)):
        selected = permitted & (backwards == reverse)
        np.minimum.at(output, segments[selected], bases[selected])
        directions[segments[selected], int(reverse)] = True
    return forward, backward, lengths, directions


def _geometry_samples(chunk: NumericChunk, selected: NDArray[np.bool_], spacing_m: float
                      ) -> tuple[FloatArray, IntArray, FloatArray, FloatArray]:
    a = chunk.arrays
    offsets = cast(IntArray, a["geometry_offsets"])
    xy = cast(FloatArray, a["geometry_xy"])
    owners = np.repeat(np.arange(chunk.counts.segments), np.diff(offsets))
    steps = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    # Remove artificial jumps between the end and start of adjacent segments.
    steps[offsets[1:-1] - 1] = 0
    cumulative = np.r_[0.0, np.cumsum(steps)]
    origins = cumulative[offsets[:-1]]
    totals = cumulative[offsets[1:] - 1] - origins
    pieces = np.flatnonzero(selected[owners[:-1]] & (owners[:-1] == owners[1:]) & (steps > 0))
    counts = np.maximum(1, np.ceil(steps[pieces] / spacing_m).astype(np.int64))
    # Include both ends so a budget boundary or snapped start cannot lose a full cell.
    repeats = counts + 1
    indices = np.repeat(pieces, repeats)
    starts = np.repeat(np.cumsum(repeats) - repeats, repeats)
    fractions = (np.arange(len(indices)) - starts) / np.repeat(counts, repeats)
    segment = owners[indices]
    points = xy[indices] + fractions[:, None] * (xy[indices + 1] - xy[indices])
    chainage = cumulative[indices] - origins[segment] + fractions * steps[indices]
    return points, segment, chainage, totals


def sample_numeric_chunk(chunk: NumericChunk, distances: NumericDistances, start: NumericEndpoint,
                         profile: int, maximum_m: float, spacing_m: float, *, support_m: float = 0) -> DistanceSamples:
    """Sample legal directed edges, including the interior virtual start."""
    if (not math.isfinite(spacing_m) or spacing_m <= 0 or not math.isfinite(maximum_m) or maximum_m <= 0
            or not math.isfinite(support_m) or support_m < 0):
        raise ValueError("Sample spacing and maximum distance must be finite and positive")
    forward, backward, lengths, directions = _segment_anchors(chunk, distances, profile)
    start_segments = chunk.arrays["segment_ids"] == start.segment_id
    selected = (np.minimum(forward, backward) <= maximum_m) | start_segments
    xy, segment, chainage, totals = _geometry_samples(chunk, selected, spacing_m)
    position = chainage * np.divide(lengths[segment], totals[segment],
                                   out=np.zeros(len(segment)), where=totals[segment] > 0)
    values = np.minimum(forward[segment] + position, backward[segment] + lengths[segment] - position)
    if start.segment_id is not None:
        delta = position - start.graph_offset_m
        from_start = start_segments[segment] & np.where(delta >= 0, directions[segment, 0], directions[segment, 1])
        values = np.minimum(values, np.where(from_start, np.abs(delta), np.inf))
    reached = values <= maximum_m + support_m
    points, values = xy[reached], values[reached]
    if np.any(start_segments & directions.any(axis=1)) and start.point is not None:
        points = np.vstack((points, start.point))
        values = np.r_[values, 0.0]
    return DistanceSamples(points, values)


def merge_distance_samples(samples: list[DistanceSamples]) -> DistanceSamples:
    if not samples:
        return DistanceSamples(np.empty((0, 2)), np.empty(0))
    return DistanceSamples(np.concatenate([sample.xy for sample in samples]),
                           np.concatenate([sample.distances for sample in samples]))


def build_distance_grid(samples: DistanceSamples, settings: ShellSettings, *,
                        deadline: float | None = None) -> DistanceGrid:
    """Interpolate nearest network samples within a bounded display radius."""
    validate_shell_settings(settings)
    if not len(samples.xy):
        raise ValueError("No reachable geometry samples")
    low = np.floor((samples.xy.min(axis=0) - settings.radius_m) / settings.cell_m) * settings.cell_m
    high = np.ceil((samples.xy.max(axis=0) + settings.radius_m) / settings.cell_m) * settings.cell_m
    shape = np.rint((high - low) / settings.cell_m).astype(np.int64) + 1
    if int(shape[0]) * int(shape[1]) > settings.max_cells:
        raise ValueError("Distance shell grid exceeds the cell budget")
    x = low[0] + np.arange(shape[0]) * settings.cell_m
    y = low[1] + np.arange(shape[1]) * settings.cell_m
    tree = cKDTree(samples.xy)
    values = np.full((len(y), len(x)), np.nan)
    # Bound temporary query storage independently of the overall grid size.
    for row in range(0, len(y), 128):
        check_deadline(deadline)
        xx, yy = np.meshgrid(x, y[row:row + 128])
        separation, nearest = tree.query(np.column_stack((xx.ravel(), yy.ravel())),
                                         distance_upper_bound=settings.radius_m)
        valid = np.isfinite(separation)
        block = values[row:row + 128].reshape(-1)
        block[valid] = samples.distances[nearest[valid]]
    return DistanceGrid(x, y, values)


def distance_bands(grid: DistanceGrid, maximum_m: float, settings: ShellSettings) -> tuple[DistanceBand, ...]:
    validate_shell_settings(settings)
    if not math.isfinite(maximum_m) or maximum_m <= 0:
        raise ValueError("Maximum shell distance must be finite and positive")
    levels = np.r_[np.arange(0.0, maximum_m, settings.interval_m), maximum_m]
    contour_levels = levels.copy()
    # Filled contours exclude their lower level; include the zero-distance start.
    contour_levels[0] = np.nextafter(0.0, -np.inf)
    generator = contourpy.contour_generator(x=grid.x, y=grid.y, z=np.ma.masked_invalid(grid.values),
                                            fill_type="OuterOffset", corner_mask=False)
    bands: list[DistanceBand] = []
    for lower, upper, (points, offsets) in zip(levels[:-1], levels[1:], generator.multi_filled(contour_levels)):
        polygons: list[tuple[FloatArray, ...]] = []
        for vertices, offsets_for_polygon in zip(points, offsets):
            rings = tuple(vertices[start:end] for start, end in zip(offsets_for_polygon[:-1], offsets_for_polygon[1:]))
            if _nonzero_ring(rings[0]):
                polygons.append((rings[0], *(ring for ring in rings[1:] if _nonzero_ring(ring))))
        bands.append(DistanceBand(float(lower), float(upper), tuple(polygons)))
    return tuple(bands)


def _nonzero_ring(ring: FloatArray) -> bool:
    """Exact threshold extrema can produce zero-area contour rings."""
    if len(ring) < 4:
        return False
    local = ring - ring[0]
    twice_area = np.dot(local[:-1, 0], local[1:, 1]) - np.dot(local[:-1, 1], local[1:, 0])
    return bool(abs(twice_area) > 1e-8)

"""Sample manifest-pinned terrain heights along ordered route traversals."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
import rasterio
from rasterio.warp import transform
from rasterio.io import DatasetReader

from runmap.api.runs import TraversalResponse
from runmap.paths.graph import geometry_length_m
from runmap.routing.geometry import projected_points
from runmap.routing.types import Segment


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    dataset_id: str = Field(max_length=100)
    legs: list[list[TraversalResponse]] = Field(min_length=1, max_length=2)


class ProfileInterval(BaseModel):
    start_m: float
    end_m: float
    start_elevation_m: float | None
    end_elevation_m: float | None
    grade_percent: float | None
    coordinates: list[tuple[float, float]]
    quality_flags: list[str]


class RouteProfile(BaseModel):
    dataset_id: str
    manifest_id: str | None
    wavelength_m: float
    sampling_interval_m: float = 10
    distance_m: float
    turnaround_m: float | None
    ascent_m: float
    descent_m: float
    valid_length_fraction: float
    totals_complete: bool
    intervals: list[ProfileInterval]


@dataclass(frozen=True)
class ProfileSpan:
    start_m: float
    end_m: float
    first: tuple[float, float]
    last: tuple[float, float]
    flags: tuple[str, ...]


def _flags(segment: Segment) -> tuple[str, ...]:
    tags = segment["tags"]
    flags = [key for key in ("bridge", "tunnel")
             if tags.get(key, "").lower() not in {"", "no", "false", "0"}]
    if tags.get("highway") == "steps":
        flags.append("stairs")
    if tags.get("area") == "yes":
        flags.append("pedestrian_area_perimeter")
    return tuple(flags)


def _segment_spans(segment: Segment, traversal: TraversalResponse, offset: float) -> list[ProfileSpan]:
    length = segment["length_m"]
    a, b = traversal.start_m, traversal.end_m
    if not all(math.isfinite(x) and 0 <= x <= length + 1e-6 for x in (a, b)):
        raise ValueError("Route traversal lies outside its segment")
    a, b = min(a, length), min(b, length)
    if abs(b - a) > 200000 or length > 200000:
        raise ValueError("Route profile exceeds 200 km")
    coordinates = segment["geometry"]
    distances = [geometry_length_m(coordinates[i:i + 2]) for i in range(len(coordinates) - 1)]
    total = sum(distances)
    if length <= 0 or total <= 0:
        raise ValueError("Route contains empty geometry")
    chainage = np.concatenate(([0.0], np.cumsum(distances))) / total * length
    low, high = sorted((a, b))
    # Fixed segment anchors keep an out-and-back's samples identical in both directions.
    anchors = set(chainage.tolist()) | set(np.linspace(0, length, math.ceil(length / 10) + 1).tolist())
    positions = [low, *sorted(x for x in anchors if low < x < high), high]
    if b < a:
        positions.reverse()
    xs, ys = zip(*coordinates)
    points = list(zip(np.interp(positions, chainage, xs).tolist(), np.interp(positions, chainage, ys).tolist()))
    flags = _flags(segment)
    return [ProfileSpan(offset + abs(x - a), offset + abs(y - a), first, last, flags)
            for x, y, first, last in zip(positions, positions[1:], points, points[1:]) if abs(y - x) > 1e-8]


def route_spans(legs: Sequence[Sequence[TraversalResponse]], segments: Mapping[str, Segment]
                ) -> tuple[list[ProfileSpan], float | None]:
    spans: list[ProfileSpan] = []
    offset = 0.0
    turnaround = None
    for index, leg in enumerate(legs):
        if not leg:
            raise ValueError("Route legs require traversals")
        for traversal in leg:
            items = _segment_spans(segments[traversal.segment_id], traversal, offset)
            if spans and items and math.dist(*projected_points([spans[-1].last, items[0].first])) > 2:
                raise ValueError("Route traversals are disconnected")
            spans.extend(items)
            offset += abs(traversal.end_m - traversal.start_m)
            if len(spans) > 30000 or offset > 200000:
                raise ValueError("Route profile exceeds 200 km or 30000 intervals")
        if index == 0 and len(legs) == 2:
            turnaround = offset
    return spans, turnaround


def _sample_height(dataset: DatasetReader, x: float, y: float) -> float | None:
    # Clamp at the core edge; adjacent cores share the same source and smoothing halo.
    col, row = ~dataset.transform * (x, y)
    col = min(max(col - .5, 0), dataset.width - 1)
    row = min(max(row - .5, 0), dataset.height - 1)
    left, top = math.floor(col), math.floor(row)
    right, bottom = min(left + 1, dataset.width - 1), min(top + 1, dataset.height - 1)
    cells = dataset.read(1, window=rasterio.windows.Window(left, top, right-left+1, bottom-top+1), masked=True)
    if np.ma.getmaskarray(cells).any() or not np.isfinite(cells).all():
        return None
    dx, dy = col - left, row - top
    return float((1-dy) * ((1-dx)*cells[0, 0] + dx*cells[0, -1])
                 + dy * ((1-dx)*cells[-1, 0] + dx*cells[-1, -1]))


def sample_heights(paths: Sequence[Path], coordinates: Sequence[tuple[float, float]]) -> list[float | None]:
    """Read four neighbouring cells for bilinear heights; keep nodata as gaps."""
    if not coordinates:
        return []
    xs, ys = transform("EPSG:4326", "EPSG:3067", *zip(*coordinates))
    values: list[float | None] = [None] * len(coordinates)
    for path in paths:
        with rasterio.open(path) as dataset:
            if dataset.crs is None or dataset.crs.to_epsg() != 3067 or dataset.count != 1:
                raise ValueError("Route height tiles require one EPSG:3067 elevation band")
            for index, (x, y) in enumerate(zip(xs, ys)):
                if values[index] is not None or not (dataset.bounds.left <= x < dataset.bounds.right
                                                    and dataset.bounds.bottom < y <= dataset.bounds.top):
                    continue
                values[index] = _sample_height(dataset, x, y)
    return values


def build_route_profile(dataset_id: str, manifest_id: str | None, wavelength_m: float,
                        legs: Sequence[Sequence[TraversalResponse]], segments: Mapping[str, Segment],
                        paths: Sequence[Path]) -> RouteProfile:
    spans, turnaround = route_spans(legs, segments)
    points = list(dict.fromkeys(point for span in spans for point in (span.first, span.last)))
    heights = dict(zip(points, sample_heights(paths, points)))
    intervals: list[ProfileInterval] = []
    ascent = descent = valid = 0.0
    for span in spans:
        first, last = heights[span.first], heights[span.last]
        change = None if first is None or last is None else last - first
        flags = list(span.flags)
        if change is None:
            flags.append("missing_terrain")
        else:
            ascent += max(0, change)
            descent += max(0, -change)
            valid += span.end_m - span.start_m
        intervals.append(ProfileInterval(
            start_m=span.start_m, end_m=span.end_m, start_elevation_m=first, end_elevation_m=last,
            grade_percent=None if change is None else 100 * change / (span.end_m - span.start_m),
            coordinates=[span.first, span.last], quality_flags=flags))
    distance = spans[-1].end_m if spans else 0.0
    return RouteProfile(dataset_id=dataset_id, manifest_id=manifest_id, wavelength_m=wavelength_m,
                        distance_m=distance, turnaround_m=turnaround, ascent_m=ascent, descent_m=descent,
                        valid_length_fraction=valid / distance if distance else 0,
                        totals_complete=bool(distance) and abs(valid-distance) < 1e-6, intervals=intervals)

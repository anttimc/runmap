"""Attach direction-dependent ascent estimates from prepared terrain gradients."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np
import rasterio
from numpy.typing import NDArray
from rasterio.warp import transform

from runmap.paths.graph import geometry_length_m
from runmap.routing.types import (
    Arc, GraphDocument, Measurement, MeasurementDocument, Segment, Tags, TerrainMetadata,
)
from runmap.elevation.store import BEARING_SCALE_DEGREES, DERIVED_NODATA, MAGNITUDE_SCALE


@dataclass(frozen=True)
class TerrainSample:
    segment_id: str
    east: float
    north: float
    length_m: float
    direction_east: float
    direction_north: float
    start_m: float
    end_m: float
    graph_start_m: float
    graph_end_m: float
    start: tuple[float, float]
    end: tuple[float, float]


@dataclass
class SegmentTotals:
    ascent_m: float = 0.0
    descent_m: float = 0.0
    samples: int = 0
    valid: int = 0


def _edge_samples(
    segment_id: str, first: tuple[float, float], second: tuple[float, float],
    chainage: float, graph_chainage: float, graph_step: float, interval_m: float,
) -> list[TerrainSample]:
    dx, dy = second[0] - first[0], second[1] - first[1]
    length = math.hypot(dx, dy)
    count = max(1, math.ceil(length / interval_m))
    step = length / count
    records = []
    for index in range(count):
        fraction = (index + 0.5) / count
        records.append(TerrainSample(
            segment_id=segment_id,
            east=first[0] + fraction * dx, north=first[1] + fraction * dy,
            length_m=step, direction_east=dx / length, direction_north=dy / length,
            start_m=chainage + index * step, end_m=chainage + (index + 1) * step,
            graph_start_m=graph_chainage + index * graph_step / count,
            graph_end_m=graph_chainage + (index + 1) * graph_step / count,
            start=(first[0] + index / count * dx, first[1] + index / count * dy),
            end=(first[0] + (index + 1) / count * dx, first[1] + (index + 1) / count * dy),
        ))
    return records


def _segment_samples(segment: Segment, interval_m: float) -> list[TerrainSample]:
    """Keep projected chainage and stored graph distance aligned along each edge."""
    longitude, latitude = zip(*segment["geometry"])
    east, north = transform("EPSG:4326", "EPSG:3067", longitude, latitude)
    chainage = graph_chainage = 0.0
    geodesic_total = geometry_length_m(segment["geometry"])
    records = []
    for vertex, (first, second) in enumerate(zip(zip(east, north), zip(east[1:], north[1:]))):
        length = math.hypot(second[0] - first[0], second[1] - first[1])
        if length <= 0:
            continue
        graph_step = geometry_length_m(segment["geometry"][vertex:vertex + 2]) * segment["length_m"] / geodesic_total
        records.extend(_edge_samples(segment["id"], first, second, chainage, graph_chainage, graph_step, interval_m))
        chainage += length
        graph_chainage += graph_step
    return records


def _intervals(graph: GraphDocument, interval_m: float) -> list[TerrainSample]:
    return [record for segment in graph["segments"] for record in _segment_samples(segment, interval_m)]


def _sample_gradients(
    paths: Iterable[str | Path], records: Sequence[TerrainSample],
) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
    values = np.full((len(records), 2), np.nan, dtype=float)
    xs = np.asarray([record.east for record in records])
    ys = np.asarray([record.north for record in records])
    assigned = np.zeros(len(records), dtype=bool)
    for path in paths:
        with rasterio.open(path) as dataset:
            if dataset.crs is None or dataset.crs.to_epsg() != 3067 or dataset.count < 2:
                raise ValueError(f"Terrain tile has an incompatible layout: {path}")
            bounds = dataset.bounds
            indices = np.flatnonzero(
                (~assigned)
                & (xs >= bounds.left) & (xs < bounds.right)
                & (ys >= bounds.bottom) & (ys < bounds.top)
            )
            if not len(indices):
                continue
            samples = np.asarray(list(dataset.sample(
                [(xs[index], ys[index]) for index in indices], indexes=(1, 2)
            )))
            valid = np.all(np.isfinite(samples), axis=1) & np.all(samples != DERIVED_NODATA, axis=1)
            if dataset.nodata is not None:
                valid &= np.all(samples != dataset.nodata, axis=1)
            valid_indices = indices[valid]
            values[valid_indices, 0] = samples[valid, 0] * MAGNITUDE_SCALE
            values[valid_indices, 1] = samples[valid, 1] * BEARING_SCALE_DEGREES
            assigned[valid_indices] = True
    return values, assigned


def _quality_flags(tags: Tags, valid: bool) -> list[str]:
    flags = [name for name in ("bridge", "tunnel")
             if str(tags.get(name, "")).lower() not in {"", "no", "false", "0"}]
    if tags.get("highway") == "steps":
        flags.append("stairs")
    if str(tags.get("area", "")).lower() in {"yes", "true", "1"}:
        flags.append("pedestrian_area_perimeter")
    if not valid:
        flags.append("missing_terrain")
    return flags


def _directional_grade(record: TerrainSample, gradient: NDArray[np.float64]) -> float:
    magnitude, bearing = gradient
    radians = math.radians(bearing)
    return float(magnitude * (math.sin(radians) * record.direction_east + math.cos(radians) * record.direction_north))


def _measurement(
    record: TerrainSample, index: int, grade: float | None, tags: Tags,
) -> Measurement:
    lon, lat = transform("EPSG:3067", "EPSG:4326",
                         [record.start[0], record.end[0]], [record.start[1], record.end[1]])
    return {
        "segment_id": record.segment_id, "interval_index": index,
        "start_m": record.start_m, "end_m": record.end_m,
        "graph_start_m": record.graph_start_m, "graph_end_m": record.graph_end_m,
        "geometry": [[lon[0], lat[0]], [lon[1], lat[1]]],
        "grade_percent": None if grade is None else grade * 100,
        "height_change_m": None if grade is None else grade * record.length_m,
        "valid": grade is not None, "quality_flags": _quality_flags(tags, grade is not None),
    }


def _accumulate_total(total: SegmentTotals, height_change: float | None) -> None:
    total.samples += 1
    if height_change is not None:
        total.ascent_m += max(0.0, height_change)
        total.descent_m += max(0.0, -height_change)
        total.valid += 1


def _collect_measurements(
    segments: Sequence[Segment], records: Sequence[TerrainSample],
    gradients: NDArray[np.float64], valid: NDArray[np.bool_],
) -> tuple[dict[str, list[Measurement]], dict[str, SegmentTotals]]:
    totals = {segment["id"]: SegmentTotals() for segment in segments}
    measurements: dict[str, list[Measurement]] = {segment["id"]: [] for segment in segments}
    by_segment = {segment["id"]: segment for segment in segments}
    for record, gradient, is_valid in zip(records, gradients, valid):
        grade = _directional_grade(record, gradient) if is_valid else None
        items = measurements[record.segment_id]
        item = _measurement(record, len(items), grade, by_segment[record.segment_id].get("tags", {}))
        items.append(item)
        # Preserve the original component-wise multiplication order for totals.
        change = None
        if is_valid:
            magnitude, bearing = gradient
            radians = math.radians(bearing)
            east, north = magnitude * math.sin(radians), magnitude * math.cos(radians)
            change = float((east * record.direction_east + north * record.direction_north) * record.length_m)
        _accumulate_total(totals[record.segment_id], change)
    return measurements, totals


def _update_segments(
    segments: Sequence[Segment], measurements: Mapping[str, list[Measurement]],
    totals: Mapping[str, SegmentTotals],
) -> None:
    for segment in segments:
        total = totals[segment["id"]]
        segment["ascent_m"] = round(total.ascent_m, 3)
        segment["descent_m"] = round(total.descent_m, 3)
        segment["terrain_sample_count"] = total.samples
        intervals = measurements[segment["id"]]
        segment["terrain_valid_fraction"] = sum(
            item["end_m"] - item["start_m"] for item in intervals if item["valid"]
        ) / sum(item["end_m"] - item["start_m"] for item in intervals) if intervals else 0.0
        segment["terrain_totals_complete"] = total.valid == total.samples


def _update_arcs(arcs: Sequence[Arc], segments: Sequence[Segment]) -> None:
    by_segment = {segment["id"]: segment for segment in segments}
    for arc in arcs:
        segment = by_segment[arc["segment_id"]]
        forward = arc["from"] == segment["from"] and arc["to"] == segment["to"]
        arc["ascent_m"] = segment["ascent_m"] if forward else segment["descent_m"]
        arc["descent_m"] = segment["descent_m"] if forward else segment["ascent_m"]
        arc["terrain_valid_fraction"] = segment["terrain_valid_fraction"]


def _terrain_metadata(
    records: Sequence[TerrainSample], valid: NDArray[np.bool_], wavelength_m: float,
    interval_m: float, manifest_id: str | None,
) -> TerrainMetadata:
    return {
        "schema_version": 1, "manifest_id": manifest_id,
        "source": "spectral-thin-plate terrain gradient", "wavelength_m": int(wavelength_m),
        "sampling_interval_m": interval_m,
        "method": "integrated directional derivative of the prepared 2D terrain surface",
        "chainage_crs": "EPSG:3067",
        "graph_chainage": "cumulative geodesic vertex lengths scaled to stored segment length",
        "profile_kind": "estimated terrain height change from each valid run start",
        "valid_fraction": sum(r.length_m for r, v in zip(records, valid) if v) / sum(r.length_m for r in records),
    }


def enrich_graph_terrain(
    graph: GraphDocument, terrain_paths: Iterable[str | Path], wavelength_m: float,
    sampling_interval_m: float = 10, *, manifest_id: str | None = None,
) -> GraphDocument:
    """Estimate path height changes from the smoothed terrain, retaining coverage gaps."""
    interval = float(sampling_interval_m)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Terrain sampling interval must be positive")
    records = _intervals(graph, interval)
    if not records:
        raise ValueError("Graph has no segment geometry to enrich")
    gradients, valid = _sample_gradients(tuple(terrain_paths), records)
    measurements, totals = _collect_measurements(graph["segments"], records, gradients, valid)
    _update_segments(graph["segments"], measurements, totals)
    _update_arcs(graph["arcs"], graph["segments"])
    metadata = _terrain_metadata(records, valid, wavelength_m, interval, manifest_id)
    graph["terrain"] = metadata
    graph["summary"]["terrain_valid_fraction"] = metadata["valid_fraction"]
    graph["_measurements"] = MeasurementDocument(schema_version=1, method=metadata.copy(), segments=measurements)
    return graph

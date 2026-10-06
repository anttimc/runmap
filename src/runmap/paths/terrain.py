"""Attach direction-dependent ascent estimates from prepared terrain gradients."""

import math

import numpy as np
import rasterio
from rasterio.warp import transform

from runmap.elevation.store import BEARING_SCALE_DEGREES, DERIVED_NODATA, MAGNITUDE_SCALE


def _intervals(graph, interval_m):
    records = []
    for segment in graph["segments"]:
        longitude, latitude = zip(*segment["geometry"])
        east, north = transform("EPSG:4326", "EPSG:3067", longitude, latitude)
        for first, second in zip(zip(east, north), zip(east[1:], north[1:])):
            dx = second[0] - first[0]
            dy = second[1] - first[1]
            length = math.hypot(dx, dy)
            if length <= 0:
                continue
            count = max(1, math.ceil(length / interval_m))
            step = length / count
            for index in range(count):
                fraction = (index + 0.5) / count
                records.append((
                    segment["id"],
                    first[0] + fraction * dx,
                    first[1] + fraction * dy,
                    step,
                    dx / length,
                    dy / length,
                ))
    return records


def _sample_gradients(paths, records):
    values = np.full((len(records), 2), np.nan, dtype=float)
    xs = np.asarray([record[1] for record in records])
    ys = np.asarray([record[2] for record in records])
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
            valid = (samples[:, 0] != DERIVED_NODATA) & (samples[:, 1] != DERIVED_NODATA)
            valid_indices = indices[valid]
            values[valid_indices, 0] = samples[valid, 0] * MAGNITUDE_SCALE
            values[valid_indices, 1] = samples[valid, 1] * BEARING_SCALE_DEGREES
            assigned[valid_indices] = True
    return values, assigned


def enrich_graph_terrain(graph, terrain_paths, wavelength_m, sampling_interval_m=10):
    """Integrate the smoothed 2D gradient along each physical segment."""
    interval = float(sampling_interval_m)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Terrain sampling interval must be positive")
    records = _intervals(graph, interval)
    if not records:
        raise ValueError("Graph has no segment geometry to enrich")
    gradients, valid = _sample_gradients(tuple(terrain_paths), records)
    totals = {
        segment["id"]: {"ascent_m": 0.0, "descent_m": 0.0, "samples": 0, "valid": 0}
        for segment in graph["segments"]
    }
    for record, gradient, is_valid in zip(records, gradients, valid):
        total = totals[record[0]]
        total["samples"] += 1
        if not is_valid:
            continue
        magnitude, bearing = gradient
        radians = math.radians(bearing)
        gradient_east = magnitude * math.sin(radians)
        gradient_north = magnitude * math.cos(radians)
        elevation_change = (gradient_east * record[4] + gradient_north * record[5]) * record[3]
        total["ascent_m"] += max(0.0, elevation_change)
        total["descent_m"] += max(0.0, -elevation_change)
        total["valid"] += 1

    for segment in graph["segments"]:
        total = totals[segment["id"]]
        segment["ascent_m"] = round(total["ascent_m"], 3)
        segment["descent_m"] = round(total["descent_m"], 3)
        segment["terrain_sample_count"] = total["samples"]
        segment["terrain_valid_fraction"] = round(
            total["valid"] / total["samples"] if total["samples"] else 0.0, 6
        )
    by_segment = {segment["id"]: segment for segment in graph["segments"]}
    for arc in graph["arcs"]:
        segment = by_segment[arc["segment_id"]]
        forward = arc["from"] == segment["from"] and arc["to"] == segment["to"]
        arc["ascent_m"] = segment["ascent_m"] if forward else segment["descent_m"]
        arc["descent_m"] = segment["descent_m"] if forward else segment["ascent_m"]
        arc["terrain_valid_fraction"] = segment["terrain_valid_fraction"]
    graph["terrain"] = {
        "source": "spectral-thin-plate terrain gradient",
        "wavelength_m": int(wavelength_m),
        "sampling_interval_m": interval,
        "method": "integrated directional derivative of the prepared 2D terrain surface",
        "valid_fraction": round(float(np.mean(valid)), 6),
    }
    graph["summary"]["terrain_valid_fraction"] = graph["terrain"]["valid_fraction"]
    return graph

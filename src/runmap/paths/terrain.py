"""Attach direction-dependent ascent estimates from prepared terrain gradients."""

import math

import numpy as np
import rasterio
from rasterio.warp import transform

from runmap.paths.graph import geometry_length_m
from runmap.elevation.store import BEARING_SCALE_DEGREES, DERIVED_NODATA, MAGNITUDE_SCALE


def _intervals(graph, interval_m):
    records = []
    for segment in graph["segments"]:
        longitude, latitude = zip(*segment["geometry"])
        east, north = transform("EPSG:4326", "EPSG:3067", longitude, latitude)
        chainage = graph_chainage = 0.0
        geodesic_total = geometry_length_m(segment["geometry"])
        for vertex, (first, second) in enumerate(zip(zip(east, north), zip(east[1:], north[1:]))):
            dx = second[0] - first[0]
            dy = second[1] - first[1]
            length = math.hypot(dx, dy)
            if length <= 0:
                continue
            graph_step = geometry_length_m(segment["geometry"][vertex:vertex + 2]) * segment["length_m"] / geodesic_total
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
                    chainage + index * step, chainage + (index + 1) * step,
                    graph_chainage + index * graph_step / count,
                    graph_chainage + (index + 1) * graph_step / count,
                    (first[0] + index / count * dx, first[1] + index / count * dy),
                    (first[0] + (index + 1) / count * dx, first[1] + (index + 1) / count * dy),
                ))
            chainage += length
            graph_chainage += graph_step
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
            valid = np.all(np.isfinite(samples), axis=1) & np.all(samples != DERIVED_NODATA, axis=1)
            if dataset.nodata is not None:
                valid &= np.all(samples != dataset.nodata, axis=1)
            valid_indices = indices[valid]
            values[valid_indices, 0] = samples[valid, 0] * MAGNITUDE_SCALE
            values[valid_indices, 1] = samples[valid, 1] * BEARING_SCALE_DEGREES
            assigned[valid_indices] = True
    return values, assigned


def enrich_graph_terrain(graph, terrain_paths, wavelength_m, sampling_interval_m=10, *, manifest_id=None):
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
    measurements = {segment["id"]: [] for segment in graph["segments"]}
    segments = {segment["id"]: segment for segment in graph["segments"]}
    for record, gradient, is_valid in zip(records, gradients, valid):
        tags = segments[record[0]].get("tags", {})
        flags = [name for name in ("bridge", "tunnel")
                 if str(tags.get(name, "")).lower() not in {"", "no", "false", "0"}]
        if tags.get("highway") == "steps":
            flags.append("stairs")
        if str(tags.get("area", "")).lower() in {"yes", "true", "1"}:
            flags.append("pedestrian_area_perimeter")
        if not is_valid:
            flags.append("missing_terrain")
        lon, lat = transform("EPSG:3067", "EPSG:4326",
                             [record[10][0], record[11][0]], [record[10][1], record[11][1]])
        grade = None
        if is_valid:
            magnitude, bearing = gradient
            radians = math.radians(bearing)
            grade = float(magnitude * (math.sin(radians) * record[4] + math.cos(radians) * record[5]))
        measurements[record[0]].append({
            "segment_id": record[0], "interval_index": len(measurements[record[0]]),
            "start_m": record[6], "end_m": record[7],
            "graph_start_m": record[8], "graph_end_m": record[9],
            "geometry": [[lon[0], lat[0]], [lon[1], lat[1]]],
            "grade_percent": None if grade is None else grade * 100,
            "height_change_m": None if grade is None else grade * record[3],
            "valid": bool(is_valid), "quality_flags": flags,
        })
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
        intervals = measurements[segment["id"]]
        segment["terrain_valid_fraction"] = sum(
            item["end_m"] - item["start_m"] for item in intervals if item["valid"]
        ) / sum(item["end_m"] - item["start_m"] for item in intervals) if intervals else 0.0
        segment["terrain_totals_complete"] = total["valid"] == total["samples"]
    by_segment = {segment["id"]: segment for segment in graph["segments"]}
    for arc in graph["arcs"]:
        segment = by_segment[arc["segment_id"]]
        forward = arc["from"] == segment["from"] and arc["to"] == segment["to"]
        arc["ascent_m"] = segment["ascent_m"] if forward else segment["descent_m"]
        arc["descent_m"] = segment["descent_m"] if forward else segment["ascent_m"]
        arc["terrain_valid_fraction"] = segment["terrain_valid_fraction"]
    graph["terrain"] = {
        "schema_version": 1,
        "manifest_id": manifest_id,
        "source": "spectral-thin-plate terrain gradient",
        "wavelength_m": int(wavelength_m),
        "sampling_interval_m": interval,
        "method": "integrated directional derivative of the prepared 2D terrain surface",
        "chainage_crs": "EPSG:3067",
        "graph_chainage": "cumulative geodesic vertex lengths scaled to stored segment length",
        "profile_kind": "estimated terrain height change from each valid run start",
        "valid_fraction": sum(r[3] for r, v in zip(records, valid) if v) / sum(r[3] for r in records),
    }
    graph["summary"]["terrain_valid_fraction"] = graph["terrain"]["valid_fraction"]
    graph["_measurements"] = {"schema_version": 1, "method": dict(graph["terrain"]), "segments": measurements}
    return graph

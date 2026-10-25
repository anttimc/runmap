"""Measure snapshot-to-shell delivery without changing the distance-tile API."""

import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import platform
import sqlite3
from time import monotonic, perf_counter

import numpy as np
from rasterio.warp import transform

from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_exploration import ExplorationLimits, NumericExploration, prepare_local_exploration
from runmap.api.sqlite_paths import database_snap
from runmap.paths.exploration import Snap
from runmap.paths.numeric_database import iter_numeric_chunks, open_numeric_database
from runmap.routing.distance_shells import (DistanceBand, DistanceSamples, ShellSettings, build_distance_grid,
    distance_bands, merge_distance_samples, sample_numeric_chunk, validate_shell_settings)
from runmap.routing.numeric_search import NumericEndpoint
from runmap.routing.profiles import exploration_profile


def shell_samples(connection: sqlite3.Connection, exploration: NumericExploration, start: NumericEndpoint,
                  profile: int, settings: ShellSettings) -> tuple[DistanceSamples, dict[str, float | int]]:
    chunks = iter(iter_numeric_chunks(connection, exploration.bounds))
    samples: list[DistanceSamples] = []
    read_ms, sample_ms, payload_bytes, count = 0.0, 0.0, 0, 0
    while True:
        started = perf_counter()
        stored = next(chunks, None)
        read_ms += (perf_counter() - started) * 1000
        if stored is None:
            break
        payload_bytes += stored.byte_size
        count += 1
        started = perf_counter()
        samples.append(sample_numeric_chunk(stored.chunk, exploration.distances.outward, start, profile,
                                             exploration.distances.maximum_m, settings.cell_m / 2))
        sample_ms += (perf_counter() - started) * 1000
    started = perf_counter()
    result = merge_distance_samples(samples)
    sample_ms += (perf_counter() - started) * 1000
    return result, {"geometry_read_ms": read_ms, "sampling_ms": sample_ms,
                    "numeric_payload_bytes": payload_bytes, "chunks": count, "samples": len(result.xy)}


def shells_geojson(bands: tuple[DistanceBand, ...]) -> tuple[dict[str, object], int]:
    features: list[dict[str, object]] = []
    vertices = 0
    for band in bands:
        polygons = []
        for rings in band.polygons:
            geographic = []
            for ring in rings:
                vertices += len(ring)
                x, y = ring[:, 0], ring[:, 1]
                lon, lat = transform("EPSG:3067", "EPSG:4326", x, y)
                geographic.append(list(zip(lon, lat)))
            polygons.append(geographic)
        if polygons:
            features.append({"type": "Feature", "properties": {"lower_m": band.lower_m, "upper_m": band.upper_m},
                             "geometry": {"type": "MultiPolygon", "coordinates": polygons}})
    return {"type": "FeatureCollection", "features": features}, vertices


def measure_shells(connection: sqlite3.Connection, exploration: NumericExploration, start: NumericEndpoint,
                   profile: int, settings: ShellSettings) -> tuple[dict[str, object], bytes]:
    started = perf_counter()
    samples, stages = shell_samples(connection, exploration, start, profile, settings)
    grid_started = perf_counter()
    grid = build_distance_grid(samples, settings)
    contour_started = perf_counter()
    bands = distance_bands(grid, exploration.distances.maximum_m, settings)
    geojson_started = perf_counter()
    geojson, vertices = shells_geojson(bands)
    encode_started = perf_counter()
    encoded = json.dumps(geojson, separators=(",", ":"), allow_nan=False).encode()
    ended = perf_counter()
    compress_started = perf_counter()
    compressed = gzip.compress(encoded, compresslevel=6, mtime=0)
    return {**stages, "grid_ms": (contour_started - grid_started) * 1000,
            "contours_ms": (geojson_started - contour_started) * 1000,
            "projection_ms": (encode_started - geojson_started) * 1000,
            "json_ms": (ended - encode_started) * 1000, "shell_total_ms": (ended - started) * 1000,
            "gzip_ms": (perf_counter() - compress_started) * 1000,
            "grid_shape": grid.values.shape, "grid_cells": grid.values.size,
            "grid_valid_cells": int(np.isfinite(grid.values).sum()), "grid_bytes": grid.values.nbytes,
            "bands": len(bands), "polygons": sum(len(band.polygons) for band in bands),
            "vertices": vertices, "geojson_bytes": len(encoded), "gzip_bytes": len(compressed)}, encoded


def benchmark(database: Path, longitude: float, latitude: float, budgets: list[float],
              cells: list[float], radius_m: float, repeats: int, surface: str, avoid_stairs: bool,
              geojson_path: Path | None) -> dict[str, object]:
    if repeats < 1:
        raise ValueError("Repeats must be positive")
    settings = [ShellSettings(cell_m=cell, radius_m=radius_m) for cell in cells]
    for setting in settings:
        validate_shell_settings(setting)
    with closing(SQLitePathData(database)) as data:
        if data.numeric is None:
            raise ValueError("Shell benchmark requires the matching numeric-routing-v2.sqlite artifact")
        snap_response = database_snap(data, longitude, latitude, surface=surface, avoid_stairs=avoid_stairs)
        if snap_response["snap"] is None:
            raise ValueError("No eligible benchmark start")
        snap = Snap(**snap_response["snap"])
        profile = data.metadata.models.profiles.index(exploration_profile(surface, avoid_stairs))
        with closing(open_numeric_database(database.with_name("numeric-routing-v2.sqlite"))) as connection:
            segment = connection.execute("SELECT number FROM identities WHERE kind='segment' AND external_id=?",
                                         (snap.segment_id,)).fetchone()
            if segment is None:
                raise ValueError("Snapped segment is missing from numeric artifact")
            x, y = transform("EPSG:4326", "EPSG:3067", [snap.longitude], [snap.latitude])
            start = NumericEndpoint(segment_id=segment[0], graph_offset_m=snap.graph_offset_m,
                                    chainage_m=snap.chainage_m, point=(x[0], y[0]))
            rows: list[dict[str, object]] = []
            for budget in budgets:
                for repeat in range(repeats):
                    started = perf_counter()
                    exploration = prepare_local_exploration(data, snap, budget, surface, avoid_stairs,
                                                            monotonic() + 30, ExplorationLimits())
                    preparation_ms = (perf_counter() - started) * 1000
                    if not isinstance(exploration, NumericExploration):
                        raise ValueError("Expected native exploration")
                    for setting in settings:
                        result, encoded = measure_shells(connection, exploration, start, profile, setting)
                        rows.append({"budget_km": budget, "repeat": repeat, "settings": asdict(setting),
                                     "preparation_ms": preparation_ms,
                                     "reached_nodes": len(exploration.distances.outward.node_ids), **result})
                        if geojson_path is not None:
                            geojson_path.parent.mkdir(parents=True, exist_ok=True)
                            geojson_path.write_bytes(encoded)
        return {"date": datetime.now(timezone.utc).isoformat(), "dataset_id": data.dataset_id,
                "python": platform.python_version(), "platform": platform.platform(),
                "start": asdict(snap), "surface": surface, "avoid_stairs": avoid_stairs,
                "notes": ["Prototype: nearest sampled network distance within radius, no added off-network cost.",
                          "No polygon simplification; shared boundaries are retained without independent ring changes.",
                          "No water or coverage mask yet; filled areas are approximate, not off-network accessibility.",
                          "Each shell result is regenerated; no sample/grid/polygon cache. OS caches uncontrolled.",
                          "Preparation includes working-set loading and Dijkstra, excludes initial snapping.",
                          "Shell total includes numeric geometry reads through GeoJSON encoding; gzip is separate.",
                          "Whole exploration output, not one viewport tile. No HTTP or browser timing.",
                          "GeoJSON output, if requested, contains the final budget/resolution/repeat."],
                "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--longitude", type=float, default=24.795)
    parser.add_argument("--latitude", type=float, default=60.216)
    parser.add_argument("--budgets", type=float, nargs="+", default=[2, 5, 10, 20])
    parser.add_argument("--cells", type=float, nargs="+", default=[50, 25])
    parser.add_argument("--radius", type=float, default=150)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--surface", choices=("all", "surfaced"), default="all")
    parser.add_argument("--allow-stairs", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--geojson", type=Path)
    args = parser.parse_args()
    report = benchmark(args.database, args.longitude, args.latitude, args.budgets, args.cells, args.radius,
                       args.repeats, args.surface, not args.allow_stairs, args.geojson)
    encoded = json.dumps(report, indent=2)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()

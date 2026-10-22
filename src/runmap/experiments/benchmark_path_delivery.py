"""Measure path delivery in a fresh reader process, excluding offline export."""

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import resource
import sys
from time import perf_counter
from typing import cast

from rasterio.warp import transform, transform_bounds

from runmap.api.paths import PathData, inspect_path, load_path_data, path_config, snap_path_start, viewport_paths
from runmap.api.path_tiles import PathTileService, projected_point
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_paths import database_inspection, database_path_config, database_snap, database_viewport
from runmap.routing.types import Bounds


def probe_delivery(database: Path, graph: Path, bounds: Bounds, legacy: bool) -> dict[str, object]:
    started = perf_counter()
    data = load_path_data(graph) if legacy else SQLitePathData(database, graph)
    report: dict[str, object] = {"storage": "json" if legacy else "sqlite", "startup_ms": (perf_counter() - started) * 1000,
                                 "bounds_epsg3067": bounds, "dataset_id": data.dataset_id}
    try:
        report.update(_probe_requests(data, bounds))
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report["reader_peak_rss_bytes"] = peak if sys.platform == "darwin" else peak * 1024
        report["legacy_loaded"] = True if legacy else data.legacy is not None
        return report
    finally:
        if isinstance(data, SQLitePathData):
            data.close()


def _probe_requests(data: PathData | SQLitePathData, bounds: Bounds) -> dict[str, object]:
    sqlite = isinstance(data, SQLitePathData)
    config = database_path_config if sqlite else path_config
    viewport = database_viewport if sqlite else viewport_paths
    snap = database_snap if sqlite else snap_path_start
    inspection = database_inspection if sqlite else inspect_path
    wgs84 = transform_bounds("EPSG:3067", "EPSG:4326", *bounds, densify_pts=21)
    lon, lat = transform("EPSG:3067", "EPSG:4326", [(bounds[0] + bounds[2]) / 2], [(bounds[1] + bounds[3]) / 2])
    report: dict[str, object] = {}
    started = perf_counter()
    config(data)
    report["config_ms"] = (perf_counter() - started) * 1000
    service = PathTileService(data)
    px, py = projected_point(lon[0], lat[0], 15, 0, 0)
    x, y = math.floor(px / 4096), math.floor(py / 4096)
    for label in ("first", "warm"):
        started = perf_counter()
        visible = viewport(data, wgs84, 15)
        report[label + "_viewport_ms"] = (perf_counter() - started) * 1000
        report["viewport_intervals"] = len(visible["features"])
        report["viewport_sha256"] = sha256(json.dumps(visible, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        started = perf_counter()
        result = snap(data, lon[0], lat[0])
        report[label + "_snap_ms"] = (perf_counter() - started) * 1000
        if result["snap"] is not None:
            started = perf_counter()
            inspection(data, result["snap"]["segment_id"])
            report[label + "_inspection_ms"] = (perf_counter() - started) * 1000
        started = perf_counter()
        tile, hit, _ = service.tile("all-no-stairs", "gradient-hidden", 15, x, y)
        report[label + "_tile_ms"] = (perf_counter() - started) * 1000
        report[label + "_tile_cache_hit"] = hit
        report["tile_bytes"] = len(tile)
        report["tile_sha256"] = sha256(tile).hexdigest()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--bounds", type=float, nargs=4, required=True)
    parser.add_argument("--legacy", action="store_true")
    args = parser.parse_args()
    report = probe_delivery(args.database, args.graph, cast(Bounds, tuple(args.bounds)), args.legacy)
    print(json.dumps(report))


if __name__ == "__main__":
    main()

"""Compare identical prepared-data queries with the display preference on and off."""

import argparse
from collections import Counter
import json
import math
import platform
from pathlib import Path
from statistics import median
from time import perf_counter
import tracemalloc
from unittest.mock import patch

from starlette.responses import JSONResponse

from runmap.api.paths import (PathData, _build_display_index, _cells,
                              load_path_data, path_config, render_paths, viewport_paths)


def _query(data: PathData, bounds: tuple[float, float, float, float], zoom: int,
           hide: bool) -> dict:
    return render_paths(viewport_paths(data, bounds, zoom, hide_building_access=hide))


def _measure(data: PathData, bounds: tuple[float, float, float, float], zoom: int,
             hide: bool, repeats: int) -> dict:
    data.profile_indexes.clear()
    elapsed = []
    for _ in range(repeats + 1):
        started = perf_counter()
        response = _query(data, bounds, zoom, hide)
        payload = JSONResponse(response).body
        elapsed.append((perf_counter() - started) * 1000)
    cells = data.display_cells if hide else data.cells
    clipped = (max(bounds[0], data.bounds[0]), max(bounds[1], data.bounds[1]),
               min(bounds[2], data.bounds[2]), min(bounds[3], data.bounds[3]))
    candidates = {i for cell in _cells(clipped) for i in cells.get(cell, ())}
    warm = sorted(elapsed[1:])
    return {"hide_building_access": hide, "candidate_intervals": len(candidates),
            "returned_intervals": response["measurement_interval_count"],
            "rendered_features": len(response["features"]),
            "vertices": sum(len(item["geometry"]["coordinates"]) for item in response["features"]),
            "response_bytes": len(payload), "cold_ms": elapsed[0],
            "warm_median_ms": median(warm), "warm_p95_ms": warm[math.ceil(0.95 * len(warm)) - 1]}


def benchmark(graph_path: Path, repeats: int) -> dict:
    load_started = perf_counter()
    with patch("runmap.api.paths._build_display_index", side_effect=lambda data: data):
        data = load_path_data(graph_path)
    load_seconds = perf_counter() - load_started
    started = perf_counter()
    filtered = _build_display_index(data)
    classification_seconds = perf_counter() - started
    tracemalloc.start()
    measured = _build_display_index(data)
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del measured
    rows = []
    for name, longitude, latitude in (("urban", 24.807, 60.219), ("forest", 24.771, 60.242)):
        for zoom, dx, dy in ((11, 0.07, 0.04), (13, 0.025, 0.013), (15, 0.008, 0.004)):
            bounds = (longitude - dx, latitude - dy, longitude + dx, latitude + dy)
            rows.append({"viewport": name, "bounds": bounds, "zoom": zoom, "mode": "gradient",
                         "runs": [_measure(filtered, bounds, zoom, hide, repeats)
                                  for hide in (False, True)]})
    samples = [{"segment_id": segment_id, "reason": item.reason,
                "branch_length_m": item.branch_length_m,
                "tags": data.segments[segment_id]["tags"],
                "geometry": data.segments[segment_id]["geometry"]}
               for segment_id, item in sorted(filtered.building_access.items())[:10]]
    return {"dataset_id": data.dataset_id, "graph_path": str(graph_path),
            "machine": platform.platform(), "python": platform.python_version(),
            "processor": platform.machine(),
            "surface": "all", "avoid_stairs": True, "warm_repeats": repeats,
            "timing_scope": "In-process API query, rendering coalescence, and JSONResponse serialization; no HTTP/browser timing.",
            "unfiltered_load_seconds": load_seconds,
            "classification_and_display_index_seconds": classification_seconds,
            "additional_retained_bytes_tracemalloc": retained,
            "additional_peak_bytes_tracemalloc": peak,
            "policy": path_config(filtered)["building_access_filter"],
            "reason_counts": dict(Counter(item.reason for item in filtered.building_access.values())),
            "hidden_examples": samples, "viewports": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    args.output.write_text(json.dumps(benchmark(args.graph, args.repeats), indent=2) + "\n")


if __name__ == "__main__":
    main()

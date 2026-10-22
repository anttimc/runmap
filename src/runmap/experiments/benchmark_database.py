"""Probe offline export and selective SQLite reads on a prepared extract."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import platform
import resource
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import cast

from runmap.paths.database import (export_network_database, network_metadata, open_network_database,
                                   query_display_intervals, query_segment_features)
from runmap.routing.types import Bounds


def probe_database(graph_path: Path, bounds: Bounds, *, include_api: bool = False) -> dict[str, object]:
    with TemporaryDirectory(prefix="runmap-sqlite-probe-") as directory:
        destination = Path(directory) / "network.sqlite"
        started = perf_counter()
        export_network_database(graph_path, destination)
        report: dict[str, object] = {
            "graph": str(graph_path), "bounds_epsg3067": bounds,
            "export_s": perf_counter() - started, "database_bytes": destination.stat().st_size,
            "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
            "system": platform.system(), "machine": platform.machine(),
        }
        started = perf_counter()
        with closing(open_network_database(destination)) as connection:
            report["open_ms"] = (perf_counter() - started) * 1000
            metadata = network_metadata(connection)
            report["dataset_id"] = metadata.dataset_id
            report["feature_identity"] = metadata.feature_identity
            report.update(_probe_queries(connection, bounds))
            report["counts"] = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                                for table in ("nodes", "segments", "measurements", "display_intervals", "hill_features")}
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report["process_peak_rss_bytes"] = peak if sys.platform == "darwin" else peak * 1024
        report["cache_note"] = "Fresh connection after export; OS cache is warm. Peak RSS includes offline import."
        if include_api:
            readers = [_probe_delivery(destination, graph_path, bounds, legacy) for legacy in (False, True)]
            report["delivery_readers"] = readers
            equivalent = all(readers[0][key] == readers[1][key] for key in ("dataset_id", "viewport_sha256", "tile_sha256"))
            report["delivery_equivalent"] = equivalent
            if not equivalent:
                raise ValueError("SQLite and JSON delivery outputs differ")
        return report


def _probe_delivery(database: Path, graph: Path, bounds: Bounds, legacy: bool) -> dict[str, object]:
    command = [sys.executable, "-m", "runmap.experiments.benchmark_path_delivery", str(database),
               "--graph", str(graph), "--bounds", *(str(value) for value in bounds)]
    if legacy:
        command.append("--legacy")
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return cast(dict[str, object], json.loads(result.stdout))


def _probe_queries(connection: sqlite3.Connection, bounds: Bounds) -> dict[str, float | int]:
    report: dict[str, float | int] = {}
    for label in ("first", "warm"):
        started = perf_counter()
        display = query_display_intervals(connection, bounds)
        report[label + "_display_ms"] = (perf_counter() - started) * 1000
        started = perf_counter()
        features = query_segment_features(connection, bounds)
        report[label + "_features_ms"] = (perf_counter() - started) * 1000
        report["selected_intervals"] = len(display)
        report["selected_segments"] = len(features)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("--bounds", type=float, nargs=4, required=True, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--api", action="store_true", help="Compare delivery and reader RSS in fresh SQLite/JSON processes")
    args = parser.parse_args()
    payload = json.dumps(probe_database(args.graph, cast(Bounds, tuple(args.bounds)), include_api=args.api), indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()

"""Compare local SQLite planning with saved full-graph route requests."""

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import platform
import resource
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal, cast

from runmap.api.paths import load_path_data
from runmap.api.runs import EndpointRequest, PreferencesRequest, RunRequest
from runmap.routing_reference.api import generate_run
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import generate_database_run
from runmap.experiments.benchmark_hill_preferences import CASES
from runmap.paths.database import export_network_database
from runmap.routing.cost_types import PreferenceLevel


def reader(database: Path, graph: Path, backend: Literal["sqlite", "json"]) -> dict[str, object]:
    started = perf_counter()
    data = SQLitePathData(database) if backend == "sqlite" else load_path_data(graph)
    startup_ms = (perf_counter() - started) * 1000
    rows: list[dict[str, object]] = []
    levels: tuple[PreferenceLevel, ...] = ("off", "high")
    try:
        for case in CASES[:3]:
            for level in levels:
                options = RunRequest(dataset_id=data.dataset_id,
                    home=EndpointRequest(longitude=case.home[0], latitude=case.home[1], segment_id=case.home_segment),
                    turnaround=EndpointRequest(longitude=case.turnaround[0], latitude=case.turnaround[1], segment_id=case.turnaround_segment),
                    preferences=PreferencesRequest(hills=level))
                result = generate_database_run(data, options) if isinstance(data, SQLitePathData) else generate_run(data, options)
                candidates = [candidate.model_dump(mode="json") for candidate in result.candidates]
                for candidate in candidates:
                    for leg in candidate["legs"]:
                        leg.pop("expanded_nodes")
                canonical = dict(status=result.status, candidates=candidates,
                                 home=None if result.home is None else asdict(result.home),
                                 turnaround=None if result.turnaround is None else asdict(result.turnaround),
                                 cost_model=result.cost_model.model_dump(mode="json"))
                rows.append(dict(case=case.name, hills=level, request=options.model_dump(mode="json"),
                    status=result.status, elapsed_s=result.elapsed_s, candidates=len(candidates),
                    response_sha256=sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                    search=None if result.search is None else result.search.model_dump(mode="json")))
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return dict(backend=backend, dataset_id=data.dataset_id, startup_ms=startup_ms, requests=rows,
                    reader_peak_rss_bytes=rss if sys.platform == "darwin" else rss * 1024)
    finally:
        if isinstance(data, SQLitePathData):
            data.close()


def benchmark(graph: Path) -> dict[str, object]:
    with TemporaryDirectory(prefix="runmap-sqlite-runs-") as directory:
        database = Path(directory) / "network.sqlite"
        started = perf_counter()
        export_network_database(graph, database)
        export_s = perf_counter() - started
        readers: list[dict[str, object]] = []
        for backend in ("sqlite", "json"):
            completed = subprocess.run([sys.executable, "-m", "runmap.experiments.benchmark_sqlite_runs",
                str(graph), "--database", str(database), "--reader", backend], check=True, capture_output=True, text=True)
            readers.append(cast(dict[str, object], json.loads(completed.stdout)))
        comparisons = [dict(case=left["case"], hills=left["hills"], equivalent=left["response_sha256"] == right["response_sha256"],
                            sqlite_status=left["status"], json_status=right["status"])
                       for left, right in zip(readers[0]["requests"], readers[1]["requests"])]
        return dict(graph=str(graph), export_s=export_s, database_bytes=database.stat().st_size,
                    python=platform.python_version(), platform=platform.platform(), readers=readers,
                    comparisons=comparisons, equivalent=all(item["equivalent"] for item in comparisons),
                    note="Separate fresh reader processes after offline export; warm OS cache. Six saved requests per reader, uncached local graphs. Hashes include status, snapped endpoints, costs, observations, and candidate geometry/traversals; exclude search expansion counts/timing/area metadata. Not HTTP, concurrent load, or a global optimality guarantee.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--reader", choices=("sqlite", "json"))
    args = parser.parse_args()
    report = reader(args.database, args.graph, args.reader) if args.reader else benchmark(args.graph)
    payload = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()

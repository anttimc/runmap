"""Measure complete planning requests with preparation, search, and output phases."""

import argparse
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import json
from pathlib import Path
import platform
import resource
import sqlite3
import subprocess
import sys
from time import perf_counter
from typing import ParamSpec, TypeVar, cast
from unittest.mock import patch

from runmap.api.runs import EndpointRequest, PreferencesRequest, RunRequest, SettingsRequest
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.numeric_data import NumericRoutingData
from runmap.api import sqlite_runs
from runmap.routing_reference import sqlite_runs as reference_runs
from runmap.experiments.latency import latency_summary
from runmap.paths import local_network
from runmap.routing.cost_types import PreferenceLevel


P = ParamSpec("P")
R = TypeVar("R")


@dataclass
class PhaseTimes:
    milliseconds: dict[str, float] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)


def _timed(name: str, function: Callable[P, R], phases: PhaseTimes) -> Callable[P, R]:
    @wraps(function)
    def measured(*args: P.args, **kwargs: P.kwargs) -> R:
        started = perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            phases.milliseconds[name] = phases.milliseconds.get(name, 0) + (perf_counter() - started) * 1000
            phases.calls[name] = phases.calls.get(name, 0) + 1
    return measured


@contextmanager
def measure_phases() -> Iterator[PhaseTimes]:
    """Instrument coarse boundaries only, keeping per-arc routing work untouched."""
    phases = PhaseTimes()
    with ExitStack() as stack:
        stack.enter_context(patch.object(sqlite_runs, "count_network_segments", _timed("segment_count", sqlite_runs.count_network_segments, phases)))
        stack.enter_context(patch.object(reference_runs, "count_network_segments", _timed("segment_count", reference_runs.count_network_segments, phases)))
        stack.enter_context(patch.object(sqlite_runs, "_snap", _timed("snap", sqlite_runs._snap, phases)))
        stack.enter_context(patch.object(reference_runs, "load_local_network", _timed("prepare", reference_runs.load_local_network, phases)))
        stack.enter_context(patch.object(local_network, "query_network", _timed("topology", local_network.query_network, phases)))
        stack.enter_context(patch.object(local_network, "query_segment_features", _timed("features", local_network.query_segment_features, phases)))
        stack.enter_context(patch.object(local_network, "build_graph_index", _timed("index", local_network.build_graph_index, phases)))
        stack.enter_context(patch.object(local_network, "_pack_hills", _timed("pack_hills", local_network._pack_hills, phases)))
        stack.enter_context(patch.object(reference_runs, "plan_run", _timed("search", reference_runs.plan_run, phases)))
        stack.enter_context(patch.object(reference_runs, "_retry_reason", _timed("boundary_check", reference_runs._retry_reason, phases)))
        stack.enter_context(patch.object(reference_runs, "render_run_result", _timed("render", reference_runs.render_run_result, phases)))
        stack.enter_context(patch.object(NumericRoutingData, "prepare", _timed("numeric_prepare", NumericRoutingData.prepare, phases)))
        stack.enter_context(patch.object(sqlite_runs, "numeric_plan_run", _timed("numeric_search", sqlite_runs.numeric_plan_run, phases)))
        stack.enter_context(patch.object(sqlite_runs, "load_route_segments", _timed("route_metadata", sqlite_runs.load_route_segments, phases)))
        stack.enter_context(patch.object(sqlite_runs, "render_numeric_result", _timed("render", sqlite_runs.render_numeric_result, phases)))
        yield phases


def _request_sample(data: SQLitePathData, options: RunRequest, timeout_s: float,
                    limits: sqlite_runs.RunAreaLimits = sqlite_runs.RunAreaLimits()) -> dict[str, object]:
    with measure_phases() as phases:
        started = perf_counter()
        response = sqlite_runs.generate_database_run(data, options, timeout_s=timeout_s, limits=limits)
        output_started = perf_counter()
        payload = response.model_dump_json()
        phases.milliseconds["json_serialization"] = (perf_counter() - output_started) * 1000
        elapsed_ms = (perf_counter() - started) * 1000
    canonical = response.model_dump(mode="json", exclude={"elapsed_s"})
    return dict(elapsed_ms=elapsed_ms, phases_ms=phases.milliseconds, phase_calls=phases.calls,
                status=response.status,
                candidates=[dict(distance_m=c.distance_m, outbound_distance_m=c.legs[0].distance_m,
                                 returning_distance_m=c.legs[1].distance_m,
                                 expanded_nodes=c.legs[0].expanded_nodes+c.legs[1].expanded_nodes)
                            for c in response.candidates], response_bytes=len(payload.encode()),
                response_sha256=hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest(),
                search=None if response.search is None else response.search.model_dump(mode="json"))


def measure_worker(database: Path, home: tuple[float, float], turnaround: tuple[float, float], *,
                   hills: PreferenceLevel = "off", avoidance_ratio: float = 0.18,
                   repeats: int = 5, timeout_s: float = 15,
                   limits: sqlite_runs.RunAreaLimits = sqlite_runs.RunAreaLimits()) -> dict[str, object]:
    if repeats < 1:
        raise ValueError("Repeat count must be positive")
    sqlite_runs.validate_run_limits(limits)
    startup = perf_counter()
    with closing(SQLitePathData(database)) as data:
        startup_ms = (perf_counter() - startup) * 1000
        options = RunRequest(dataset_id=data.dataset_id, home=EndpointRequest(longitude=home[0], latitude=home[1]),
            turnaround=EndpointRequest(longitude=turnaround[0], latitude=turnaround[1]),
            preferences=PreferencesRequest(hills=hills), settings=SettingsRequest(avoidance_ratio=avoidance_ratio))
        first = _request_sample(data, options, timeout_s, limits)
        samples = [_request_sample(data, options, timeout_s, limits) for _ in range(repeats)]
        elapsed = [cast(float, sample["elapsed_ms"]) for sample in samples]
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return dict(backend="numeric" if data.numeric is not None else "reference",
                    numeric_resident_bytes=(data.numeric.resident_bytes + data.numeric.chunk_store.resident_bytes
                                            if data.numeric is not None else 0),
                    max_segments=limits.max_segments, max_retries=limits.max_retries,
                    dataset_id=data.dataset_id, request=options.model_dump(mode="json"), startup_ms=startup_ms,
                    first_request=first, repeated_requests=samples, repeated_summary=latency_summary(elapsed),
                    equivalent=all(sample["response_sha256"] == first["response_sha256"] for sample in samples),
                    peak_rss_bytes=rss if sys.platform == "darwin" else rss * 1024,
                    python=platform.python_version(), machine=platform.machine(), sqlite=sqlite3.sqlite_version,
                    note="Fresh worker; OS cache is uncontrolled. No exact-response cache. Full request timings include JSON serialization but exclude HTTP transport. Preparation contains topology/features/index/hill phases; do not sum nested phases. First/repeated timing samples are not a cold-disk or production percentile guarantee.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--home", type=float, nargs=2, default=(24.795, 60.216))
    parser.add_argument("--turnaround", type=float, nargs=2, default=(24.815, 60.225))
    parser.add_argument("--hills", choices=("off", "low", "high"), default="off")
    parser.add_argument("--avoidance-ratio", type=float, default=.18)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--timeout-s", type=float, default=15)
    parser.add_argument("--max-segments", type=int, default=sqlite_runs.RunAreaLimits().max_segments)
    parser.add_argument("--max-retries", type=int, default=sqlite_runs.RunAreaLimits().max_retries)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        report = measure_worker(args.database, (args.home[0], args.home[1]),
                                (args.turnaround[0], args.turnaround[1]), hills=args.hills,
                                avoidance_ratio=args.avoidance_ratio, repeats=args.repeats, timeout_s=args.timeout_s,
                                limits=sqlite_runs.RunAreaLimits(max_segments=args.max_segments, max_retries=args.max_retries))
    else:
        command = [sys.executable, "-m", "runmap.experiments.benchmark_run_latency", str(args.database),
                   "--home", *(str(v) for v in args.home), "--turnaround", *(str(v) for v in args.turnaround),
                   "--hills", args.hills, "--avoidance-ratio", str(args.avoidance_ratio),
                   "--repeats", str(args.repeats), "--timeout-s", str(args.timeout_s),
                   "--max-segments", str(args.max_segments), "--max-retries", str(args.max_retries), "--worker"]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        report = cast(dict[str, object], json.loads(completed.stdout))
    payload = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()

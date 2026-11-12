"""Measure ordered routing edits with explicit cache, input, and worker lifetimes."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import resource
import sqlite3
from statistics import median
import subprocess
import sys
from threading import Barrier
from time import perf_counter
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from runmap import _routing_native
from runmap.api.runs import EndpointRequest, PreferencesRequest, RunRequest, SettingsRequest
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import RunAreaLimits
from runmap.experiments.benchmark_run_latency import _request_sample
from runmap.experiments.latency import latency_summary


class EditScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    home: tuple[float, float]
    turnaround: tuple[float, float]
    preferences: PreferencesRequest = Field(default_factory=PreferencesRequest)
    settings: SettingsRequest = Field(default_factory=SettingsRequest)


@dataclass(frozen=True)
class EditBenchmarkSettings:
    cache_bytes: int = 128 * 1024**2
    repeats: int = 3
    timeout_s: float = 15
    environment_path: Path | None = None
    environment_raster_bundle: Path | None = None
    concurrency: int = 0


def _request(data: SQLitePathData, scenario: EditScenario) -> RunRequest:
    return RunRequest(dataset_id=data.dataset_id,
        home=EndpointRequest(longitude=scenario.home[0], latitude=scenario.home[1]),
        turnaround=EndpointRequest(longitude=scenario.turnaround[0], latitude=scenario.turnaround[1]),
        preferences=scenario.preferences, settings=scenario.settings)


def _cache_state(data: SQLitePathData) -> dict[str, int]:
    if data.numeric is None:
        raise ValueError("Routing edit measurements require a matching numeric artifact")
    return dict(graphs=len(data.numeric.graphs), graph_bytes=data.numeric.resident_bytes,
                chunks=len(data.numeric.chunk_store.chunks), chunk_bytes=data.numeric.chunk_store.resident_bytes,
                total_bytes=data.numeric.resident_bytes + data.numeric.chunk_store.resident_bytes)


def _measure_edit(data: SQLitePathData, scenario: EditScenario, settings: EditBenchmarkSettings, *,
                  instrument: bool = True) -> dict[str, object]:
    request = _request(data, scenario)
    before = _cache_state(data) if instrument else None
    sample = _request_sample(data, request, settings.timeout_s, instrument=instrument)
    return dict(name=scenario.name, request=request.model_dump(mode="json"),
                cache_before=before, cache_after=_cache_state(data) if instrument else None, **sample)


def _summarize(samples: list[dict[str, object]]) -> dict[str, object]:
    phases = [cast(dict[str, float], sample["phases_ms"]) for sample in samples]
    names = sorted({name for row in phases for name in row})
    return dict(**latency_summary([cast(float, sample["elapsed_ms"]) for sample in samples]),
                sample_count=len(samples), statuses=[sample["status"] for sample in samples],
                equivalent=len({cast(str, sample["response_sha256"]) for sample in samples}) == 1,
                median_phases_ms={name: median(row.get(name, 0) for row in phases) for name in names})


def _concurrent_samples(data: SQLitePathData, scenarios: tuple[EditScenario, ...],
                        settings: EditBenchmarkSettings) -> list[list[dict[str, object]]]:
    if settings.concurrency == 0:
        return []
    batches: list[list[dict[str, object]]] = []
    with ThreadPoolExecutor(max_workers=2) as workers:
        for _ in range(settings.repeats):
            barrier = Barrier(2)

            def measure(scenario: EditScenario) -> dict[str, object]:
                barrier.wait(timeout=30)
                return _measure_edit(data, scenario, settings, instrument=False)

            # Global phase patches are deliberately absent while requests overlap.
            batches.append(list(workers.map(measure, (scenarios[0], scenarios[-1]))))
    return batches


def measure_edits(database: Path, scenarios: tuple[EditScenario, ...],
                  settings: EditBenchmarkSettings = EditBenchmarkSettings()) -> dict[str, object]:
    if not scenarios or len({s.name for s in scenarios}) != len(scenarios):
        raise ValueError("Scenarios must be nonempty and uniquely named")
    if settings.repeats < 1 or settings.cache_bytes < 0 or settings.concurrency not in (0, 2):
        raise ValueError("Invalid edit benchmark settings")
    started = perf_counter()
    with closing(SQLitePathData(database, numeric_cache_bytes=settings.cache_bytes,
                               environment_path=settings.environment_path,
                               environment_raster_bundle=settings.environment_raster_bundle)) as data:
        startup_ms = (perf_counter() - started) * 1000
        first = [_measure_edit(data, scenario, settings) for scenario in scenarios]
        repeated = [[_measure_edit(data, scenario, settings) for scenario in scenarios]
                    for _ in range(settings.repeats)]
        concurrent = _concurrent_samples(data, scenarios, settings)
        summaries = {scenario.name: _summarize([row[index] for row in repeated])
                     for index, scenario in enumerate(scenarios)}
        concurrent_summaries = ({scenario.name: _summarize([row[index] for row in concurrent])
                                 for index, scenario in enumerate((scenarios[0], scenarios[-1]))}
                                if concurrent else {})
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return dict(dataset_id=data.dataset_id, feature_identity=data.metadata.feature_identity,
                    startup_ms=startup_ms, cache_budget_bytes=settings.cache_bytes, final_cache=_cache_state(data),
                    environment_path=str(settings.environment_path) if settings.environment_path else None,
                    environment_raster_bundle=str(settings.environment_raster_bundle) if settings.environment_raster_bundle else None,
                    limits=RunAreaLimits().__dict__, timeout_s=settings.timeout_s,
                    first_pass=first, repeated_passes=repeated, summaries=summaries,
                    concurrent_batches=concurrent, concurrent_summaries=concurrent_summaries,
                    peak_rss_bytes=rss if sys.platform == "darwin" else rss * 1024,
                    python=platform.python_version(), machine=platform.machine(), sqlite=sqlite3.sqlite_version,
                    native_sha256=hashlib.sha256(Path(_routing_native.__file__).read_bytes()).hexdigest(),
                    note="Fresh worker, serial ordered edits, then optional two-request batches. Only the first edit starts with empty application caches. OS cache is uncontrolled. Timings include JSON serialization; HTTP, browser rendering, and debounce are excluded. Phase timers are nested; concurrent requests disable global instrumentation. Percentiles from few samples are descriptive, not production acceptance.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--cache-mib", type=int, default=128)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout-s", type=float, default=15)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--environment-raster-bundle", type=Path)
    parser.add_argument("--concurrency", type=int, choices=(0, 2), default=0)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.worker:
        forwarded = sys.argv[1:]
        completed = subprocess.run([sys.executable, "-m", __spec__.name, *forwarded, "--worker"],
                                   stdout=subprocess.PIPE, text=True, check=True)
        print(completed.stdout, end="")
        return
    scenarios = tuple(TypeAdapter(list[EditScenario]).validate_json(args.scenarios.read_text()))
    report = measure_edits(args.database, scenarios, EditBenchmarkSettings(
        cache_bytes=args.cache_mib * 1024**2, repeats=args.repeats, timeout_s=args.timeout_s,
        environment_path=args.environment, environment_raster_bundle=args.environment_raster_bundle,
        concurrency=args.concurrency))
    payload = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()

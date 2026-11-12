"""Measure fixed-window routing against an explicitly selected native release build."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from functools import partial
import importlib.util
import json
from pathlib import Path
from statistics import median
import sys
from time import monotonic, perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runmap.api.sqlite_data import SQLitePathData
    from runmap.experiments.profile_native import RoutingResult
    from runmap.routing.numeric_search import LoadedNumericGraph, NumericEndpoint


@dataclass(frozen=True)
class BenchmarkInputs:
    graph: LoadedNumericGraph
    start: NumericEndpoint
    destination: NumericEndpoint


@dataclass(frozen=True)
class Measurement:
    median_ms: float
    samples_ms: list[float]
    hashes: list[str]
    size: dict[str, object]


def _load_native(path: Path) -> None:
    spec = importlib.util.spec_from_file_location("runmap._routing_native", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load native library: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "profile_begin"):
        raise ValueError("Use a release library built without the profiling feature")
    sys.modules["runmap._routing_native"] = module


def _prepare_inputs(data: SQLitePathData) -> BenchmarkInputs:
    from runmap.api.runs import EndpointRequest, RunRequest
    from runmap.api.sqlite_runs import _snap
    from runmap.experiments.benchmark_native_routing import DEFAULT_BOUNDS
    from runmap.routing.prepared_inputs import InputRequirements

    request = RunRequest(
        dataset_id=data.dataset_id,
        home=EndpointRequest(longitude=24.795, latitude=60.216),
        turnaround=EndpointRequest(longitude=24.815, latitude=60.225),
    )
    snaps = (_snap(data, request, True, monotonic() + 60),
             _snap(data, request, False, monotonic() + 60))
    assert data.numeric is not None and snaps[0] is not None and snaps[1] is not None
    graph, start, destination = data.numeric.prepare(
        DEFAULT_BOUNDS, snaps, monotonic() + 60,
        requirements=InputRequirements(hills=True, environment=True, signals=True),
    )
    return BenchmarkInputs(graph, start, destination)


def _workloads(inputs: BenchmarkInputs) -> dict[str, Callable[[], RoutingResult]]:
    from runmap.routing.cost_types import PreferenceSettings
    from runmap.routing.numeric_planner import numeric_plan_run
    from runmap.routing.numeric_search import numeric_distances
    from runmap.routing.run_types import RunSettings

    settings = {
        "distance": (RunSettings(avoidance_ratio=0), PreferenceSettings()),
        "shape": (RunSettings(), PreferenceSettings()),
        "hills_high": (RunSettings(), PreferenceSettings(hills="high")),
        "traffic_high": (RunSettings(), PreferenceSettings(environment_traffic="high")),
        "combined": (RunSettings(), PreferenceSettings(
            hills="high", environment_greenery="high", environment_water="high",
            environment_traffic="high", environment_wetland="high")),
    }
    assert inputs.start.point is not None and inputs.destination.point is not None
    workloads: dict[str, Callable[[], RoutingResult]] = {
        name: partial(
            numeric_plan_run, inputs.graph, inputs.start, inputs.destination,
            inputs.start.point, inputs.destination.point, run_settings, preferences=preferences,
        ) for name, (run_settings, preferences) in settings.items()
    }
    for reverse in (False, True):
        name = "explore_reverse" if reverse else "explore_forward"
        workloads[name] = partial(numeric_distances, inputs.graph, inputs.start, 3000, reverse=reverse)
    return workloads


def _measure(function: Callable[[], RoutingResult], repeats: int) -> Measurement:
    from runmap.experiments.profile_native import _fingerprint, _result_size

    result = function()
    samples: list[float] = []
    hashes: set[str] = set()
    for _ in range(repeats):
        started = perf_counter()
        result = function()
        samples.append((perf_counter() - started) * 1000)
        hashes.add(_fingerprint(result))
    return Measurement(median(samples), samples, sorted(hashes), _result_size(result))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=60)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    _load_native(args.native)
    from runmap.api.sqlite_data import SQLitePathData

    root = Path("data/paths/prepared/helsinki-capital-area")
    with closing(SQLitePathData(
        root / "network.sqlite", environment_path=root / "environment-join.sqlite",
        environment_raster_bundle=Path("data/environment/prepared/helsinki-capital-area-v3"),
    )) as data:
        workloads = _workloads(_prepare_inputs(data))
        results: dict[str, Measurement] = {}
        for name, function in workloads.items():
            results[name] = _measure(function, args.repeats)
            print(name, round(results[name].median_ms, 3), flush=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({name: asdict(row) for name, row in results.items()}, indent=2) + "\n")


if __name__ == "__main__":
    main()

"""Separate native routing phase timings from Rust allocation traffic on real data."""

import argparse
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import resource
import subprocess
import sys
from time import monotonic, perf_counter
from typing import TypeVar

import numpy as np

from runmap import _routing_native
from runmap.api.runs import EndpointRequest, RunRequest
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import _snap
from runmap.experiments.benchmark_native_routing import DEFAULT_BOUNDS, _endpoint
from runmap.experiments.latency import latency_summary
from runmap.paths.database import network_metadata
from runmap.paths.environment_join import open_environment_join
from runmap.paths.numeric_database import open_numeric_database
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.geometry import projected_points
from runmap.routing.numeric_planner import NumericRunResult, numeric_plan_run
from runmap.routing.numeric_search import LoadedNumericGraph, NumericDistances, load_numeric_graph, numeric_distances
from runmap.routing.run_types import RunSettings


T = TypeVar("T")
ProfileRows = list[tuple[str, int, int, int, int]]
RoutingResult = NumericRunResult | NumericDistances


@dataclass(frozen=True)
class PhaseSample:
    milliseconds: float
    calls: int
    allocations: int
    requested_bytes: int


def _fingerprint(value: RoutingResult) -> str:
    """Hash all result fields, including arrays, outside the measured interval."""
    def encode(item: object) -> object:
        if isinstance(item, np.ndarray):
            return item.tolist()
        raise TypeError(f"Cannot serialize {type(item)}")
    return hashlib.sha256(json.dumps(asdict(value), default=encode, sort_keys=True).encode()).hexdigest()


def _measure(function: Callable[[], T], mode: str, instrumented: bool) -> tuple[T, float, ProfileRows]:
    if instrumented:
        _routing_native.profile_begin(mode in ("detail", "allocations"), mode != "allocations", mode == "allocations")
    started = perf_counter()
    try:
        result = function()
    finally:
        elapsed = (perf_counter() - started) * 1000
        rows = _routing_native.profile_finish() if instrumented else []
    return result, elapsed, rows


def _result_size(result: RoutingResult) -> dict[str, object]:
    if isinstance(result, NumericDistances):
        return dict(expanded_nodes=result.expanded_nodes, reached_nodes=len(result.node_ids),
                    output_bytes=sum(array.nbytes for array in (result.node_ids, result.values, result.frontier_node_ids)))
    legs = [leg for candidate in result.candidates for leg in candidate.legs]
    return dict(expanded_nodes=sum(leg.expanded_nodes for leg in legs), searches=len(legs),
                path_edges=sum(len(leg.arc_ids) for leg in legs), outcomes=result.outcomes,
                environment_penalties={field: sum(float(leg.environment_costs[field].sum()) for leg in legs)
                                       for field in legs[0].environment_costs} if legs else {},
                output_bytes=sum(array.nbytes for leg in legs for array in (
                    leg.arc_ids, leg.segment_ids, leg.start_m, leg.end_m, leg.components,
                    leg.hill_features, leg.frontier_node_ids,
                    *leg.environment_costs.values(), *leg.environment_unknown_m.values())))


def _samples(function: Callable[[], RoutingResult], repeats: int, mode: str, instrumented: bool) -> dict[str, object]:
    elapsed: list[float] = []
    phases: dict[str, list[PhaseSample]] = {}
    hashes: set[str] = set()
    for _ in range(repeats):
        result, duration, rows = _measure(function, mode, instrumented)
        hashes.add(_fingerprint(result))
        elapsed.append(duration)
        for name, nanoseconds, calls, allocations, size in rows:
            phases.setdefault(name, []).append(PhaseSample(nanoseconds / 1e6, calls, allocations, size))
    return dict(elapsed_ms=elapsed, summary=latency_summary(elapsed), fingerprints=sorted(hashes),
                result=_result_size(result),
                phases={name: [asdict(row) for row in values] for name, values in phases.items()})


def _environment_graph(numeric: Path, environment: Path, raster_bundle: Path,
                       instrumented: bool) -> tuple[LoadedNumericGraph, dict[str, object]]:
    """Prepare a separate joined graph so historical workloads retain their inputs."""
    manifest = json.loads((raster_bundle / "manifest.json").read_text())
    with closing(open_numeric_database(numeric)) as connection:
        metadata = network_metadata(connection)
        identity = dict(network_dataset_id=metadata.dataset_id,
                        network_feature_identity=metadata.feature_identity,
                        raster_dataset_id=manifest["dataset_id"],
                        raster_sha256=manifest["artifacts"][0]["sha256"])
        with closing(open_environment_join(environment, **identity)) as joined:
            graph, duration, rows = _measure(
                lambda: load_numeric_graph(connection, DEFAULT_BOUNDS, environment_connection=joined),
                "allocations" if instrumented else "baseline", instrumented)
    return graph, dict(identity=identity, environment_bytes=graph.environment_bytes,
                       prepare_elapsed_ms=duration, prepare_phases=rows)


def profile(database: Path, numeric: Path, repeats: int, instrumented: bool,
            environment: Path | None = None, raster_bundle: Path | None = None) -> dict[str, object]:
    if repeats < 1:
        raise ValueError("Repeat count must be positive")
    if (environment is None) != (raster_bundle is None):
        raise ValueError("Provide both the environment join and raster bundle")
    if instrumented and not hasattr(_routing_native, "profile_begin"):
        raise RuntimeError("Build the extension with --features profiling first")
    if not instrumented and hasattr(_routing_native, "profile_begin"):
        raise RuntimeError("Build the normal extension without profiling for the baseline")
    options = RunRequest(dataset_id="profile", home=EndpointRequest(longitude=24.795, latitude=60.216),
                         turnaround=EndpointRequest(longitude=24.815, latitude=60.225))
    with closing(SQLitePathData(database)) as data, closing(open_numeric_database(numeric)) as connection:
        if data.metadata.feature_identity != network_metadata(connection).feature_identity:
            raise ValueError("Source and numeric artifact identities differ")
        first = _snap(data, options, True, monotonic() + 60)
        last = _snap(data, options, False, monotonic() + 60)
        if first is None or last is None:
            raise ValueError("Endpoints could not be snapped")
        start = _endpoint(connection, first)
        destination = _endpoint(connection, last)
        points = projected_points([[first.longitude, first.latitude], [last.longitude, last.latitude]])
        segments = tuple(endpoint.segment_id for endpoint in (start, destination) if endpoint.segment_id is not None)
        graph, prepare_ms, prepare_rows = _measure(lambda: load_numeric_graph(connection, DEFAULT_BOUNDS, hill_segments=segments),
                                                   "allocations" if instrumented else "baseline", instrumented)
    workloads: dict[str, Callable[[], RoutingResult]] = {
        "distance_1km": lambda: numeric_distances(graph, start, 1000),
        "distance_3km": lambda: numeric_distances(graph, start, 3000),
        "distance_only_roundtrip": lambda: numeric_plan_run(graph, start, destination, points[0], points[1],
                                                            RunSettings(avoidance_ratio=0)),
        "shape_roundtrip": lambda: numeric_plan_run(graph, start, destination, points[0], points[1]),
        "shape_hills_high_roundtrip": lambda: numeric_plan_run(graph, start, destination, points[0], points[1],
                                                               preferences=PreferenceSettings(hills="high")),
    }
    environment_prepare: dict[str, object] | None = None
    if environment is not None and raster_bundle is not None:
        joined_graph, environment_prepare = _environment_graph(numeric, environment, raster_bundle, instrumented)
        workloads.update({
            "shape_environment_off_roundtrip": lambda: numeric_plan_run(
                joined_graph, start, destination, points[0], points[1]),
            "shape_environment_traffic_high_roundtrip": lambda: numeric_plan_run(
                joined_graph, start, destination, points[0], points[1],
                preferences=PreferenceSettings(environment_traffic="high")),
        })
    results: dict[str, object] = {}
    for name, function in workloads.items():
        function()  # Warm up; the immutable graph is reused, search state and costs are recomputed.
        modes = ("coarse", "detail", "allocations") if instrumented else ("baseline",)
        results[name] = {mode: _samples(function, repeats, mode, instrumented) for mode in modes}
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return dict(created_utc=datetime.now(timezone.utc).isoformat(),
                revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                platform=platform.platform(), processor=platform.processor(), python=platform.python_version(),
                instrumented=instrumented, repeats=repeats, dataset_id=graph.metadata.dataset_id,
                bounds=DEFAULT_BOUNDS, home=(24.795, 60.216), turnaround=(24.815, 60.225),
                nodes=graph.native.node_count, segments=graph.native.segment_count,
                chunks=graph.chunk_count, payload_bytes=graph.buffer_bytes,
                prepare_elapsed_ms=prepare_ms, payload_load_ms=graph.payload_load_ms,
                index_prepare_ms=graph.index_prepare_ms, prepare_phases=prepare_rows,
                edge_layout_bytes=_routing_native.profile_layout() if instrumented else None,
                peak_rss_bytes=rss if sys.platform == "darwin" else rss * 1024,
                environment_prepare=environment_prepare,
                workloads=results,
                note="Fixed loaded working area; no HTTP, retries, route geometry rendering, or response cache. "
                     "Normal baseline and instrumented builds run in separate processes. Timings are exclusive. "
                     "Detailed timing includes per-edge timer overhead; allocations are a separate timer-free pass. "
                     "Requested bytes sum alloc/alloc_zeroed/full new realloc sizes, not live bytes, RSS, or copied bytes. "
                     "Python/SQLite allocator requests are excluded; Rust serialization staging buffers are included. "
                     "Graph preparation is sampled once per process, with uncontrolled OS cache; not a cold-disk measurement.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("numeric", type=Path)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--instrumented", action="store_true")
    parser.add_argument("--environment", type=Path, help="Prepared environment join; adds off/high traffic workloads")
    parser.add_argument("--environment-raster-bundle", type=Path, help="Raster bundle used to validate the join")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = profile(args.database, args.numeric, args.repeats, args.instrumented,
                     args.environment, args.environment_raster_bundle)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Wrote {args.report}")


if __name__ == "__main__":
    main()

"""Measure native run and distance primitives separately from HTTP integration."""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import resource
import sqlite3
import sys
from time import monotonic, perf_counter

from runmap.api.runs import EndpointRequest, PreferencesRequest, RunRequest, SettingsRequest
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import _snap
from runmap.experiments.latency import latency_summary
from runmap.paths.database import network_metadata, open_network_database
from runmap.paths.exploration import Snap
from runmap.paths.local_network import load_local_network
from runmap.paths.numeric_database import open_numeric_database
from runmap.paths.numeric_hills import read_hill_intervals
from runmap.routing.cost_types import PreferenceLevel, PreferenceSettings
from runmap.routing.geometry import projected_points
from runmap.routing.numeric_planner import NumericRunResult, numeric_plan_run
from runmap.routing.numeric_search import LoadedNumericGraph, NumericEndpoint, load_numeric_graph, numeric_distances
from runmap.routing.profiles import exploration_profile
from runmap.routing_reference.run_planner import plan_run
from runmap.routing.run_types import RunSettings
from runmap.routing.types import Bounds


DEFAULT_BOUNDS: Bounds = (374971.56657009746, 6675095.112818664, 381827.50090309826, 6680883.1672423985)


def _endpoint(connection: sqlite3.Connection, snap: Snap, *, include_hill_intervals: bool = False) -> NumericEndpoint:
    row = connection.execute("SELECT number FROM identities WHERE kind='segment' AND external_id=?", (snap.segment_id,)).fetchone()
    if row is None:
        raise ValueError("Snapped segment is missing from numeric identities")
    return NumericEndpoint(segment_id=int(row[0]), graph_offset_m=snap.graph_offset_m, chainage_m=snap.chainage_m,
                           hill_intervals=read_hill_intervals(connection, int(row[0])) if include_hill_intervals else None)


def _canonical_result(result: NumericRunResult) -> str:
    rows = [dict(left=c.outbound_left,
                 legs=[dict(cost=p.cost, arcs=p.arc_ids.tolist(), segments=p.segment_ids.tolist(),
                            starts=p.start_m.tolist(), ends=p.end_m.tolist(), components=p.components.tolist())
                       for p in c.legs]) for c in result.candidates]
    return hashlib.sha256(json.dumps(dict(outcomes=result.outcomes, candidates=rows), sort_keys=True).encode()).hexdigest()


def _compare_reference(database: Path, bounds: Bounds, options: RunRequest, home: Snap, turnaround: Snap,
                       actual: NumericRunResult) -> dict[str, object]:
    started = perf_counter()
    with closing(open_network_database(database)) as connection:
        local = load_local_network(connection, bounds, exploration_profile(options.surface, options.avoid_stairs),
                                   include_measurements=False, include_coverage=False)
    prepare_ms = (perf_counter()-started)*1000
    started = perf_counter()
    expected = plan_run(local.routing, home, turnaround, RunSettings(**options.settings.model_dump()),
                        preferences=PreferenceSettings(**options.preferences.model_dump()), hills=local.hills,
                        frontier_nodes=local.frontier_indices)
    differences = [abs(a.cost-b.cost)
        for native, original in zip(actual.candidates, expected.candidates)
        for a, b in zip(native.legs, (leg.path for leg in original.legs))]
    equivalent = (actual.outcomes == expected.outcomes and len(actual.candidates) == len(expected.candidates)
                  and all(d < .001 for d in differences))
    return dict(equivalent_costs=equivalent, maximum_cost_difference=max(differences, default=0),
                reference_prepare_ms=prepare_ms, reference_search_ms=(perf_counter()-started)*1000)


def _sample_runs(graph: LoadedNumericGraph, first: NumericEndpoint, last: NumericEndpoint,
                 points: list[tuple[float, float]], options: RunRequest,
                 repeats: int) -> tuple[NumericRunResult, list[float], bool]:
    settings = RunSettings(**options.settings.model_dump())
    preferences = PreferenceSettings(**options.preferences.model_dump())
    profile = exploration_profile(options.surface, options.avoid_stairs)
    samples: list[float] = []
    hashes: set[str] = set()
    for _ in range(repeats):
        started = perf_counter()
        result = numeric_plan_run(graph, first, last, points[0], points[1], settings,
                                  profile=profile, preferences=preferences)
        samples.append((perf_counter()-started)*1000)
        hashes.add(_canonical_result(result))
    return result, samples, len(hashes) == 1


def _sample_distances(graph: LoadedNumericGraph, start: NumericEndpoint, options: RunRequest,
                      repeats: int) -> tuple[list[float], int]:
    profile = exploration_profile(options.surface, options.avoid_stairs)
    samples: list[float] = []
    for _ in range(repeats):
        started = perf_counter()
        result = numeric_distances(graph, start, 1000, profile=profile)
        samples.append((perf_counter()-started)*1000)
    return samples, len(result.node_ids)


def benchmark_native_routing(database: Path, numeric: Path, bounds: Bounds = DEFAULT_BOUNDS, *,
                             home: tuple[float, float] = (24.795, 60.216),
                             turnaround: tuple[float, float] = (24.815, 60.225),
                             hills: PreferenceLevel = "off", avoidance_ratio: float = .18,
                             repeats: int = 10, compare_reference: bool = False) -> dict[str, object]:
    if repeats < 1:
        raise ValueError("Repeat count must be positive")
    startup = perf_counter()
    with closing(SQLitePathData(database)) as data, closing(open_numeric_database(numeric)) as connection:
        if data.metadata.feature_identity != network_metadata(connection).feature_identity:
            raise ValueError("Reference and numeric feature identities differ")
        startup_ms = (perf_counter()-startup)*1000
        options = RunRequest(dataset_id=data.dataset_id,
            home=EndpointRequest(longitude=home[0], latitude=home[1]),
            turnaround=EndpointRequest(longitude=turnaround[0], latitude=turnaround[1]),
            preferences=PreferencesRequest(hills=hills), settings=SettingsRequest(avoidance_ratio=avoidance_ratio))
        started = perf_counter()
        home_snap, turnaround_snap = _snap(data, options, True, monotonic()+60), _snap(data, options, False, monotonic()+60)
        if home_snap is None or turnaround_snap is None:
            raise ValueError("Benchmark endpoints have no eligible snap")
        first = _endpoint(connection, home_snap, include_hill_intervals=hills != "off")
        last = _endpoint(connection, turnaround_snap, include_hill_intervals=hills != "off")
        points = projected_points([[home_snap.longitude, home_snap.latitude], [turnaround_snap.longitude, turnaround_snap.latitude]])
        snap_ms = (perf_counter()-started)*1000
        started = perf_counter()
        graph = load_numeric_graph(connection, bounds)
        prepare_ms = (perf_counter()-started)*1000
    result, samples, deterministic = _sample_runs(graph, first, last, points, options, repeats)
    distance_samples, reached = _sample_distances(graph, first, options, repeats)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    comparison = _compare_reference(database, bounds, options, home_snap, turnaround_snap, result) if compare_reference else None
    return dict(dataset_id=graph.metadata.dataset_id, request=options.model_dump(mode="json"), bounds_epsg3067=bounds,
                chunks=graph.chunk_count, buffer_bytes=graph.buffer_bytes,
                nodes=graph.native.node_count, segments=graph.native.segment_count,
                metadata_startup_ms=startup_ms, snapping_ms=snap_ms, numeric_prepare_ms=prepare_ms,
                payload_load_ms=graph.payload_load_ms, native_index_ms=graph.index_prepare_ms,
                native_run_ms=samples, native_run_summary=latency_summary(samples),
                numeric_first_working_set_ms=snap_ms+prepare_ms+samples[0],
                outcomes=result.outcomes, candidates=len(result.candidates),
                reached_frontier=result.frontier_node_ids.tolist(), deterministic=deterministic,
                native_distance_summary=latency_summary(distance_samples), distance_reached_nodes=reached,
                comparison=comparison, peak_rss_bytes=rss if sys.platform == "darwin" else rss*1024,
                note="Experimental fixed-window primitive benchmark. Preparation includes BLOB reads, native validation and native indexing. Run samples reuse the immutable graph but compute routes/costs again; no exact-response cache. Includes both assignments and both legs when shape is enabled. No automatic window expansion, HTTP rendering/serialization, or residency cache. First working set includes snap+prepare+first search; OS cache uncontrolled. Cost comparisons allow 0.001 m numerical tolerance; native numeric-ID ties can choose a different equal-cost path.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("numeric", type=Path)
    parser.add_argument("--bounds", type=float, nargs=4, default=DEFAULT_BOUNDS)
    parser.add_argument("--home", type=float, nargs=2, default=(24.795, 60.216))
    parser.add_argument("--turnaround", type=float, nargs=2, default=(24.815, 60.225))
    parser.add_argument("--hills", choices=("off", "low", "high"), default="off")
    parser.add_argument("--avoidance-ratio", type=float, default=.18)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--compare-reference", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    bounds = (args.bounds[0], args.bounds[1], args.bounds[2], args.bounds[3])
    report = benchmark_native_routing(args.database, args.numeric, bounds,
        home=(args.home[0], args.home[1]), turnaround=(args.turnaround[0], args.turnaround[1]),
        hills=args.hills, avoidance_ratio=args.avoidance_ratio, repeats=args.repeats, compare_reference=args.compare_reference)
    payload = json.dumps(report, indent=2)+"\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()

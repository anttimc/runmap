"""Record metadata packing memory and output signatures in isolated processes."""

import argparse
from collections.abc import Mapping
from dataclasses import asdict, fields, is_dataclass
import hashlib
import json
from pathlib import Path
import platform
import random
import resource
from statistics import median
import sys
from time import perf_counter

import numpy as np

from runmap.api.paths import PathData, inspect_path, load_path_data, path_policy_index, viewport_paths
from runmap.api.path_tiles import PathTileService, encode_tile, projected_point
from runmap.paths.exploration import snap_candidates
from runmap.paths.measurements import graph_digest, inspect_measurements, segment_measurements
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.run_planner import plan_run
from runmap.routing.run_types import RunSettings
from runmap.routing.search import GraphData, build_graph_index, indexed_shortest_distances, route_coordinates
from runmap.routing.serialization import prepared_json


def retained(value: object, seen: set[int]) -> int:
    """Count shared array owners once without materializing lazy record views."""
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        size += sum(retained(getattr(value, field.name), seen) for field in fields(value))
        if hasattr(value, '__dict__'):
            size += sys.getsizeof(value.__dict__)
    elif isinstance(value, Mapping):
        size += sum(retained(key, seen) + retained(item, seen) for key, item in value.items())
    elif isinstance(value, (tuple, list, set, frozenset)):
        size += sum(retained(item, seen) for item in value)
    elif isinstance(value, memoryview):
        size += retained(value.obj, seen)
    elif isinstance(value, np.ndarray) and value.base is not None:
        size += retained(value.base, seen)
    return size


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=prepared_json)
    return hashlib.sha256(encoded.encode()).hexdigest()


def measurement_outputs(data: PathData) -> dict[str, object]:
    measurement_hash = hashlib.sha256()
    for segment in data.measurements.segment_ids:
        measurement_hash.update(digest(segment_measurements(data.measurements, segment)).encode())
    assert data.hills is not None
    hills_hash = hashlib.sha256()
    for values in (data.hills.interval_offsets, data.hills.chainage, data.hills.features, data.hills.totals):
        hills_hash.update(values.tobytes())
    inspections: dict[str, str] = {}
    for segment in random.Random(42).sample(list(data.measurements.segment_ids), 100):
        items = segment_measurements(data.measurements, segment)
        inspections[f'{segment}:metadata'] = digest(inspect_path(data, segment))
        if not items:
            continue
        length = items[-1]['end_m']
        for reverse in (False, True):
            for partial in (False, True):
                inspections[f'{segment}:{reverse}:{partial}'] = digest(inspect_measurements(
                    data.measurements, segment, reverse, .17*length if partial else 0,
                    .83*length if partial else length))
    return dict(measurements=measurement_hash.hexdigest(), hills=hills_hash.hexdigest(), inspections=inspections)


def tile_outputs(data: PathData, repeats: int) -> dict[str, object]:
    service = PathTileService(data, preparation_deadline=120)
    tiles: dict[str, str] = {}
    timings: dict[str, float] = {}
    viewports: dict[str, str] = {}
    for surface in ('all', 'surfaced'):
        policy = f'{surface}-no-stairs'
        snapshots = []
        for budget in (3, 10):
            result = service.prepare(24.795, 60.216, budget, surface, True, 50, None)
            snapshots.append(service.snapshot(result['snapshot_id']))
        for lon, lat in ((24.807, 60.219), (24.771, 60.242)):
            viewports[f'{surface}:{lon}'] = digest(viewport_paths(
                data, (lon-.008, lat-.004, lon+.008, lat+.004), 15, surface))
            for z in (13, 15):
                px, py = projected_point(lon, lat, z, 0, 0)
                x, y = int(px//4096), int(py//4096)
                for variant in ('gradient-all', 'gradient-hidden'):
                    key = f'{policy}:{lon}:{z}:{variant}:static'
                    tiles[key] = hashlib.sha256(encode_tile(data, policy, variant, z, x, y)).hexdigest()
                    samples: list[float] = []
                    for _ in range(repeats):
                        started = perf_counter()
                        encode_tile(data, policy, variant, z, x, y)
                        samples.append((perf_counter()-started)*1000)
                    timings[key] = median(samples)
                for snapshot in snapshots:
                    for variant in ('gradient-hidden', 'distance-hidden'):
                        key = f'{policy}:{lon}:{z}:{variant}:{snapshot.budget_km}'
                        tiles[key] = hashlib.sha256(encode_tile(data, policy, variant, z, x, y, snapshot)).hexdigest()
    return dict(tiles=tiles, tile_median_ms=timings, viewports=viewports)


def planner_outputs(data: PathData) -> dict[str, object]:
    index = path_policy_index(data)
    home, _ = snap_candidates(data, index, 24.795, 60.216)
    assert home is not None
    results: dict[str, str] = {}
    timings: dict[str, float] = {}
    for longitude, latitude in ((24.805, 60.220), (24.815, 60.225), (24.835, 60.230)):
        turnaround, _ = snap_candidates(data, index, longitude, latitude)
        assert turnaround is not None
        for width in (0, .18, .35):
            for hills in ('off', 'high'):
                key = f'{longitude}:{latitude}:{width}:{hills}'
                result = plan_run(index, home, turnaround, RunSettings(avoidance_ratio=width),
                                  preferences=PreferenceSettings(hills=hills), hills=data.hills)
                results[key] = digest(dict(candidates=[asdict(candidate) for candidate in result.candidates],
                                           outcomes=result.outcomes, preferences=asdict(result.preferences),
                                           coordinates=[(route_coordinates(result.overlay.graph, c.outbound.arc_ids),
                                                         route_coordinates(result.overlay.graph, c.returning.arc_ids))
                                                        for c in result.candidates]))
                timings[key] = result.elapsed_s*1000
    return dict(planners=results, planner_ms=timings)


def search_outputs(data: PathData, repeats: int) -> dict[str, object]:
    outputs: dict[str, str] = {}
    timings: dict[str, float] = {}
    for surface in ('all', 'surfaced'):
        index = path_policy_index(data, surface)
        home, _ = snap_candidates(data, index, 24.795, 60.216)
        assert home is not None and index.integer is not None
        start = index.integer.node_indices[index.segments[home.segment_id]['from']]
        for budget in (1000, 10000, 30000):
            for reverse in (False, True):
                key = f'{surface}:{budget}:{reverse}'
                values: list[float] = []
                for _ in range(repeats):
                    started = perf_counter()
                    result = indexed_shortest_distances(index, start, budget, reverse=reverse)
                    values.append((perf_counter()-started)*1000)
                outputs[key] = hashlib.sha256(result.values.tobytes()+result.reached.tobytes()).hexdigest()
                timings[key] = median(values)
    return dict(searches=outputs, search_median_ms=timings)


def memory_report(index: GraphData) -> dict[str, int]:
    seen: set[int] = set()
    graph = retained(index.document, seen)
    projected = retained(index.projected, seen)
    integer = retained(index.integer, seen)
    other = retained(index, seen)
    return dict(graph_bytes=graph, projected_bytes=projected, integer_bytes=integer,
                other_index_bytes=other, total_bytes=graph+projected+integer+other)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('graph', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--graph-only', action='store_true')
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    started = perf_counter()
    if args.graph_only:
        index = build_graph_index(json.loads(args.graph.read_text()))
        data = None
    else:
        data = load_path_data(args.graph)
        index = data.routing
    loaded_ms = (perf_counter()-started)*1000
    report: dict[str, object] = dict(dataset_id=graph_digest(index.document),
                                    python=platform.python_version(), platform=platform.platform(),
                                    load_ms=loaded_ms, nodes=len(index.nodes), segments=len(index.segments),
                                    arcs=len(index.arcs), memory=memory_report(index))
    if data is not None:
        report['path_data_bytes'] = retained(data, set())
        report.update(measurement_outputs(data))
        print('Measurement and inspection signatures recorded', flush=True)
        report.update(tile_outputs(data, args.repeats))
        print('Tile signatures recorded', flush=True)
        report.update(planner_outputs(data))
        report.update(search_outputs(data, args.repeats))
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report['peak_rss_bytes'] = peak if sys.platform == 'darwin' else peak * 1024
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(f'Wrote {args.output}', flush=True)


if __name__ == '__main__':
    main()

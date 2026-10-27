"""Compare reference-search integer bookkeeping with the string-indexed baseline."""

import argparse
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import platform
from statistics import median
from time import perf_counter
import tracemalloc
from unittest.mock import patch

from runmap.api.paths import PathData, load_path_data, path_policy_index
from runmap.experiments.benchmark_integration import Timing, verify_routes
from runmap.experiments.search_reference import shortest_path_string
from runmap.paths.exploration import snap_candidates
from runmap.routing.types import Point
from runmap.routing.fields import integrate_fields
from runmap.routing_reference.fields import integrate_fields_python
from runmap.routing.integer_graph import prepare_integer_graph
from runmap.routing_reference.run_planner import distance_heuristic, endpoint_overlay, plan_run
from runmap.routing.run_types import RunSettings
from runmap.routing.graph import GraphData
from runmap.routing_reference.search import shortest_path
from runmap.routing.types import Arc


@dataclass(frozen=True)
class Scenario:
    name: str
    home: Point
    turnaround: Point
    settings: RunSettings = RunSettings()


@contextmanager
def string_planner() -> Iterator[None]:
    # Both planners now need shared integer feature IDs; compare search bookkeeping.
    with patch('runmap.routing_reference.run_planner.shortest_path', shortest_path_string):
        yield


def _timing(values: list[float]) -> Timing:
    return Timing(median(values), sorted(values)[math.ceil(.95 * len(values)) - 1])


def measure_pair(first: Callable[[], object], second: Callable[[], object],
                  repeats: int, first_context: Callable[[], AbstractContextManager[None]] = nullcontext,
                  ) -> tuple[Timing, Timing]:
    operations = (first, second)
    values: tuple[list[float], list[float]] = ([], [])
    for position, operation in enumerate(operations):
        with first_context() if position == 0 else nullcontext():
            operation()
    for iteration in range(repeats):
        for position in ((0, 1) if iteration % 2 == 0 else (1, 0)):
            with first_context() if position == 0 else nullcontext():
                started = perf_counter()
                operations[position]()
                values[position].append((perf_counter() - started) * 1000)
    return _timing(values[0]), _timing(values[1])


def measure_preparation(index: GraphData) -> dict[str, float | int]:
    elapsed: list[float] = []
    for _ in range(3):
        started = perf_counter()
        prepared = prepare_integer_graph(index.nodes, index.segments, index.arcs)
        elapsed.append((perf_counter() - started) * 1000)
        del prepared
    tracemalloc.start()
    prepared = prepare_integer_graph(index.nodes, index.segments, index.arcs)
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {'median_ms': median(elapsed), 'retained_bytes': retained, 'peak_bytes': peak,
            'nodes': len(prepared.node_ids), 'arcs': len(prepared.arc_ids), 'segments': len(prepared.segment_ids)}


def benchmark_scenario(data: PathData, index: GraphData, scenario: Scenario,
                        repeats: int) -> dict[str, object]:
    home, _ = snap_candidates(data, index, *scenario.home)
    turnaround, _ = snap_candidates(data, index, *scenario.turnaround)
    if home is None or turnaround is None:
        raise ValueError(f'Endpoints do not snap: {scenario.name}')

    def integer_route() -> object:
        return plan_run(index, home, turnaround, scenario.settings)

    expected = plan_run(index, home, turnaround, scenario.settings)
    with string_planner():
        actual = plan_run(index, home, turnaround, scenario.settings)
    verify_routes(expected, actual)
    if [(c.legs[0].path.expanded_nodes, c.legs[1].path.expanded_nodes) for c in expected.candidates] != [
            (c.legs[0].path.expanded_nodes, c.legs[1].path.expanded_nodes) for c in actual.candidates]:
        raise ValueError('Expansion counts differ')
    string_time, integer_time = measure_pair(integer_route, integer_route, repeats, string_planner)

    overlay = endpoint_overlay(index, home, turnaround)
    heuristic = distance_heuristic(overlay.graph, None)

    def length_cost(arc: Arc) -> float:
        return float(arc['length_m'])

    def integer_search() -> object:
        return shortest_path(overlay.graph, overlay.start, overlay.destination,
                             heuristic=heuristic, arc_cost=length_cost)

    def string_search() -> object:
        return shortest_path_string(overlay.graph, overlay.start, overlay.destination,
                                    heuristic=heuristic, arc_cost=length_cost)

    if integer_search() != string_search():
        raise ValueError('Length-only searches differ')
    string_search_time, integer_search_time = measure_pair(string_search, integer_search, repeats)
    return {'scenario': asdict(scenario), 'paths_costs_and_expansions_match': True,
            'string_route': asdict(string_time), 'integer_route': asdict(integer_time),
            'string_length_search': asdict(string_search_time),
            'integer_length_search': asdict(integer_search_time)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', type=Path, default=Path('data/paths/prepared/leppavaara-flat-10k/graph.json'))
    parser.add_argument('--repeats', type=int, default=20)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    print('Loading graph...', flush=True)
    data = load_path_data(args.graph)
    index = path_policy_index(data)
    profile = Scenario('profile', (24.795, 60.216), (24.815, 60.225))
    snapped_home, _ = snap_candidates(data, index, *profile.home)
    if snapped_home is None:
        raise ValueError('Profile home does not snap')
    scenarios = (replace(profile, name='shorter', turnaround=(24.805, 60.220)), profile,
                 replace(profile, name='longer', turnaround=(24.835, 60.230)),
                 replace(profile, name='moved-start',
                         home=(snapped_home.longitude + .0001, snapped_home.latitude + .0001)),
                 replace(profile, name='narrow-centre', settings=RunSettings(radius_ratio=.08)),
                 replace(profile, name='shape-off', settings=RunSettings(avoidance_ratio=0)))
    results: list[dict[str, object]] = []
    for scenario in scenarios:
        with patch('runmap.routing_reference.cost_preparation.integrate_fields', integrate_fields):
            result = benchmark_scenario(data, index, scenario, args.repeats)
        results.append(result)
        print(json.dumps(result), flush=True)
    with patch('runmap.routing_reference.cost_preparation.integrate_fields', integrate_fields_python):
        python_result = benchmark_scenario(data, index, profile, args.repeats)
    with args.graph.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'platform': platform.platform(), 'python': platform.python_version(),
              'graph': str(args.graph), 'graph_sha256': digest, 'repeats': args.repeats,
              'preparation': measure_preparation(index),
              'native_integration_routes': results, 'python_integration_profile': python_result}
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()

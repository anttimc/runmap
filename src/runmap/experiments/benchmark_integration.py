"""Compare Python and native integration on a real planner request."""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import platform
from statistics import median
from time import perf_counter
from unittest.mock import patch

from runmap.api.paths import load_path_data, path_policy_index
from runmap.paths.exploration import snap_candidates
from runmap.routing.fields import Point, integrate_fields, integrate_fields_python
from runmap.routing.run_planner import plan_run
from runmap.routing.run_types import RunResult, RunSettings

Integrals = tuple[float, float, float]
Integrator = Callable[[Sequence[Point], Point, Point, float, float, float, float | None], Integrals]


@dataclass(frozen=True)
class IntegrationCase:
    points: tuple[Point, ...]
    home: Point
    destination: Point
    radius: float
    width: float
    spacing: float


@dataclass(frozen=True)
class Timing:
    median_ms: float
    p95_ms: float


def measure(operation: Callable[[], object], repeats: int) -> Timing:
    operation()
    times: list[float] = []
    for _ in range(repeats):
        started = perf_counter()
        operation()
        times.append((perf_counter() - started) * 1000)
    return Timing(median(times), sorted(times)[math.ceil(.95 * repeats) - 1])


def evaluate(cases: Sequence[IntegrationCase], integrator: Integrator) -> list[Integrals]:
    return [integrator(c.points, c.home, c.destination, c.radius, c.width, c.spacing, None)
            for c in cases]


def verify_routes(expected: RunResult, actual: RunResult) -> None:
    if expected.outcomes != actual.outcomes or len(expected.candidates) != len(actual.candidates):
        raise ValueError("Planner outcomes differ")
    for first, second in zip(expected.candidates, actual.candidates):
        for a, b in ((first.legs[0].path, second.legs[0].path), (first.legs[1].path, second.legs[1].path)):
            if a.arc_ids != b.arc_ids or not math.isclose(a.cost, b.cost, rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError("Planner paths or costs differ")


def benchmark(graph: Path, repeats: int) -> dict[str, object]:
    with graph.open('rb') as stream:
        graph_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    data = load_path_data(graph)
    index = path_policy_index(data)
    home, _ = snap_candidates(data, index, 24.795, 60.216)
    turnaround, _ = snap_candidates(data, index, 24.815, 60.225)
    if home is None or turnaround is None:
        raise ValueError("Profile endpoints do not snap to this dataset")
    cases: list[IntegrationCase] = []

    def record(points: Sequence[Point], home: Point, destination: Point, radius: float,
               width: float, spacing: float = 5.0, deadline: float | None = None) -> Integrals:
        cases.append(IntegrationCase(tuple(points), home, destination, radius, width, spacing))
        return integrate_fields(points, home, destination, radius, width, spacing, deadline)

    def route() -> RunResult:
        return plan_run(index, home, turnaround, RunSettings())

    with patch('runmap.routing.cost_preparation.integrate_fields', record):
        native_result = route()
    expected = evaluate(cases, integrate_fields_python)
    actual = evaluate(cases, integrate_fields)
    for a, b in zip(expected, actual):
        if not all(math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-9) for x, y in zip(a, b)):
            raise ValueError("Integration results differ")
    with patch('runmap.routing.cost_preparation.integrate_fields', integrate_fields_python):
        verify_routes(route(), native_result)
        python_route = measure(route, repeats)
    native_route = measure(route, repeats)
    return {
        'platform': platform.platform(), 'python': platform.python_version(),
        'graph': str(graph), 'graph_bytes': graph.stat().st_size,
        'graph_sha256': graph_digest,
        'endpoints': [[24.795, 60.216], [24.815, 60.225]],
        'settings': asdict(RunSettings()), 'repeats': repeats,
        'geometry_count': len(cases), 'routes_and_integrals_match': True,
        'python_integration': asdict(measure(lambda: evaluate(cases, integrate_fields_python), repeats)),
        'native_integration_with_conversion': asdict(measure(lambda: evaluate(cases, integrate_fields), repeats)),
        'python_route': asdict(python_route), 'native_route': asdict(native_route),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', type=Path, default=Path('data/paths/prepared/leppavaara-flat-10k/graph.json'))
    parser.add_argument('--repeats', type=int, default=20)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    result = benchmark(args.graph, args.repeats)
    report = json.dumps(result, indent=2) + '\n'
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    print(report, end='')


if __name__ == '__main__':
    main()

"""Compare smooth loop routing around hard obstacles on one Delaunay graph."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter

from runmap.experiments.smooth_loops import (
    Point, Settings, SpatialGraph, path_edges, path_length, plan_outing,
    plot_experiment, random_delaunay_graph,
)

Rectangle = tuple[float, float, float, float]


@dataclass(frozen=True)
class Scenario:
    name: str
    obstacles: tuple[Rectangle, ...]


def intersects_rectangle(first: Point, second: Point, rectangle: Rectangle) -> bool:
    """Clip the full edge against a closed rectangle, including boundary contact."""
    xmin, ymin, xmax, ymax = rectangle
    lower, upper = 0.0, 1.0
    for origin, delta, minimum, maximum in (
        (first[0], second[0] - first[0], xmin, xmax),
        (first[1], second[1] - first[1], ymin, ymax),
    ):
        if delta == 0:
            if origin < minimum or origin > maximum:
                return False
            continue
        near, far = sorted(((minimum - origin) / delta, (maximum - origin) / delta))
        lower, upper = max(lower, near), min(upper, far)
        if lower > upper:
            return False
    return True


def remove_obstructed_edges(graph: SpatialGraph, obstacles: tuple[Rectangle, ...]) -> SpatialGraph:
    """Preserve node IDs while removing every edge that touches a hard obstacle."""
    return SpatialGraph(graph.points, tuple(
        tuple(neighbour for neighbour in neighbours if not any(
            intersects_rectangle(graph.points[node], graph.points[neighbour], rectangle)
            for rectangle in obstacles
        )) for node, neighbours in enumerate(graph.neighbours)
    ))


def scenarios() -> tuple[Scenario, ...]:
    return (
        Scenario("open", ()),
        Scenario("blocked-left", ((30, 49, 70, 72),)),
        Scenario("narrow-crossing", ((48, 0, 52, 37), (48, 43, 52, 80))),
        Scenario("narrow-access", ((0, 0, 28, 38), (0, 42, 28, 80))),
        Scenario("disconnected", ((48, 0, 52, 80),)),
    )


def run_scenario(graph: SpatialGraph, scenario: Scenario, output: Path) -> list[dict[str, object]]:
    filtered = remove_obstructed_edges(graph, scenario.obstacles)
    runs = []
    records: list[dict[str, object]] = []
    for strength in (2.0, 8.0):
        settings = Settings(strength)
        for outbound_left in (True, False):
            started = perf_counter()
            result = plan_outing(filtered, 0, 1, settings, outbound_left)
            record: dict[str, object] = {
                "scenario": scenario.name, "centre_strength": strength,
                "outbound_left": outbound_left, "seconds": perf_counter() - started,
                "status": "unreachable" if result is None else "complete",
            }
            if result is not None:
                outbound, returning = result
                if outbound_left:
                    runs.append((settings, outbound, returning))
                shared = path_edges(outbound.path) & path_edges(returning.path)
                record.update({
                    "length": path_length(filtered, outbound.path) + path_length(filtered, returning.path),
                    "reused_length": sum(path_length(filtered, edge) for edge in shared),
                    "outbound_expansions": len(outbound.expansions),
                    "return_expansions": len(returning.expansions),
                    "outbound_path": outbound.path, "return_path": returning.path,
                })
            records.append(record)
            print(record["scenario"], strength, outbound_left, record["status"],
                  f"length={record.get('length')}, reused={record.get('reused_length')}")
    if runs:
        plot_experiment(filtered, 0, 1, runs, output / f"{scenario.name}.png", scenario.obstacles)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/experiments/smooth-loop-obstacles"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--samples", type=int, default=8000)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    graph = random_delaunay_graph(args.samples, 100, 80, seed=args.seed, anchors=((10, 40), (90, 40)))
    records = [record for scenario in scenarios() for record in run_scenario(graph, scenario, args.output_dir)]
    (args.output_dir / "results.json").write_text(json.dumps({
        "seed": args.seed, "samples": args.samples, "points": graph.points,
        "neighbours": graph.neighbours,
        "obstacles": {scenario.name: scenario.obstacles for scenario in scenarios()},
        "runs": records,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()

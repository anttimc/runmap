"""Inspect smooth route penalties on randomly sampled Delaunay graphs."""

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import heapq
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.spatial import Delaunay

from runmap.routing_reference.fields import smooth_fields, smooth_fields_array

Point = tuple[float, float]
Edge = tuple[int, int]


@dataclass(frozen=True)
class SpatialGraph:
    points: tuple[Point, ...]
    neighbours: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class Settings:
    centre_strength: float
    side_strength: float = 2.0
    reuse_strength: float = 4.0
    radius: float = 12.0
    transition_width: float = 4.0


@dataclass(frozen=True)
class SearchResult:
    path: tuple[int, ...]
    cost: float
    expansions: tuple[tuple[int, float], ...]


def random_delaunay_graph(
    sample_count: int, width: float, height: float, *, seed: int = 42,
    anchors: tuple[Point, ...] = (),
) -> SpatialGraph:
    """Include exact endpoints before triangulation; sorted adjacency makes runs repeatable."""
    if sample_count < 3 or any(not math.isfinite(v) or v <= 0 for v in (width, height)):
        raise ValueError("At least three samples and positive finite dimensions are required")
    if len(set(anchors)) != len(anchors) or any(
        not (0 <= x <= width and 0 <= y <= height) for x, y in anchors
    ):
        raise ValueError("Anchors must be distinct and within the sampling rectangle")
    samples = np.random.default_rng(seed).uniform((0, 0), (width, height), size=(sample_count, 2))
    points = anchors + tuple((float(x), float(y)) for x, y in samples)
    triangulation = Delaunay(np.asarray(points))
    neighbours: list[set[int]] = [set() for _ in points]
    for triangle in triangulation.simplices:
        a, b, c = (int(node) for node in triangle)
        for first, second in ((a, b), (b, c), (c, a)):
            neighbours[first].add(second)
            neighbours[second].add(first)
    return SpatialGraph(points, tuple(tuple(sorted(nodes)) for nodes in neighbours))


def astar(
    grid: SpatialGraph, start: int, destination: int,
    edge_cost: Callable[[int, int], float],
) -> SearchResult | None:
    """Record expansion order and actual cost for later search visualizations."""
    distances = {start: 0.0}
    parents: dict[int, int] = {}
    queue = [(math.dist(grid.points[start], grid.points[destination]), 0.0, start)]
    expansions: list[tuple[int, float]] = []
    while queue:
        _, cost, node = heapq.heappop(queue)
        if cost != distances.get(node):
            continue
        expansions.append((node, cost))
        if node == destination:
            path = [node]
            while path[-1] != start:
                path.append(parents[path[-1]])
            return SearchResult(tuple(reversed(path)), cost, tuple(expansions))
        for neighbour in grid.neighbours[node]:
            weight = edge_cost(node, neighbour)
            if not math.isfinite(weight) or weight < math.dist(grid.points[node], grid.points[neighbour]):
                raise ValueError("Edge cost must be finite and at least geometric distance")
            candidate = cost + weight
            if candidate < distances.get(neighbour, math.inf):
                distances[neighbour] = candidate
                parents[neighbour] = node
                priority = candidate + math.dist(grid.points[neighbour], grid.points[destination])
                heapq.heappush(queue, (priority, candidate, neighbour))
    return None


def penalty_fields(point: Point, home: Point, destination: Point, settings: Settings) -> tuple[float, float]:
    return smooth_fields(point, home, destination, settings.radius, settings.transition_width)


def edge_weights(
    grid: SpatialGraph, home: int, destination: int, settings: Settings, discourage_right: bool,
    reused: frozenset[Edge] = frozenset(),
) -> dict[Edge, float]:
    """Integrate smooth fields at intervals of at most one metre."""
    values = (settings.centre_strength, settings.side_strength, settings.reuse_strength)
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("Penalty strengths must be finite and non-negative")
    if any(not math.isfinite(value) or value <= 0 for value in (settings.radius, settings.transition_width)):
        raise ValueError("Field scales must be finite and positive")
    if home == destination:
        raise ValueError("Home and destination must differ")
    edges = [(node, neighbour) for node, neighbours in enumerate(grid.neighbours)
             for neighbour in neighbours if neighbour >= node]
    weights: dict[Edge, float] = {}
    points = np.asarray(grid.points, dtype=np.float64)
    for start in range(0, len(edges), 1024):
        batch = edges[start:start + 1024]
        nodes = np.asarray(batch)
        first = points[nodes[:, 0]]
        delta = points[nodes[:, 1]] - first
        # Match A*'s geometric lower bound exactly, including float rounding.
        lengths = np.fromiter((math.dist(grid.points[a], grid.points[b]) for a, b in batch),
                              dtype=np.float64, count=len(batch))
        counts = np.maximum(4, np.ceil(lengths).astype(np.intp))
        offsets = np.concatenate(([0], np.cumsum(counts)))
        sample_edges = np.repeat(np.arange(len(batch)), counts)
        fractions = (np.arange(offsets[-1]) - offsets[sample_edges] + 0.5) / counts[sample_edges]
        samples = first[sample_edges] + fractions[:, None] * delta[sample_edges]
        radial, right = smooth_fields_array(samples[:, 0], samples[:, 1],
                                            grid.points[home], grid.points[destination],
                                            settings.radius, settings.transition_width)
        side = right if discourage_right else 1 - right
        penalties = np.add.reduceat(settings.centre_strength * radial + settings.side_strength * side,
                                    offsets[:-1]) / counts
        for edge, length, penalty in zip(batch, lengths, penalties):
            node, neighbour = edge
            weight = float(length * (1 + penalty + settings.reuse_strength * (edge in reused)))
            weights[edge] = weights[(neighbour, node)] = weight
    return weights


def path_edges(path: tuple[int, ...]) -> frozenset[Edge]:
    return frozenset(tuple(sorted((first, second))) for first, second in zip(path, path[1:]))


def plan_outing(
    grid: SpatialGraph, home: int, destination: int, settings: Settings, outbound_left: bool = True,
) -> tuple[SearchResult, SearchResult] | None:
    outbound_weights = edge_weights(grid, home, destination, settings, outbound_left)
    outbound = astar(grid, home, destination, lambda a, b: outbound_weights[a, b])
    if outbound is None:
        return None
    return_weights = edge_weights(grid, home, destination, settings, not outbound_left, path_edges(outbound.path))
    returning = astar(grid, destination, home, lambda a, b: return_weights[a, b])
    return (outbound, returning) if returning is not None else None


def path_length(grid: SpatialGraph, path: tuple[int, ...]) -> float:
    return sum(math.dist(grid.points[a], grid.points[b]) for a, b in zip(path, path[1:]))


def plot_experiment(
    grid: SpatialGraph, home: int, destination: int,
    runs: list[tuple[Settings, SearchResult, SearchResult]], output: Path,
    obstacles: tuple[tuple[float, float, float, float], ...] = (),
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Rectangle

    figure, axes = plt.subplots(len(runs), 5, figsize=(20, 4 * len(runs)), squeeze=False,
                                constrained_layout=True)
    for row, (settings, outbound, returning) in enumerate(runs):
        for column, axis in enumerate(axes[row]):
            for xmin, ymin, xmax, ymax in obstacles:
                axis.add_patch(Rectangle((xmin, ymin), xmax - xmin, ymax - ymin,
                                         color="#999999", alpha=0.5))
            trace = outbound if column < 3 else returning
            fraction = (0.25, 0.6, 1.0, 1.0, 1.0)[column]
            count = max(1, math.ceil(len(trace.expansions) * fraction))
            if column < 4:
                nodes = [node for node, _ in trace.expansions[:count]]
                axis.scatter([grid.points[n][0] for n in nodes], [grid.points[n][1] for n in nodes],
                             c=range(count), cmap="viridis", s=6, vmin=0, vmax=len(trace.expansions))
            paths = [(outbound.path, "#df6600"), (returning.path, "#0077bb")] if column == 4 else []
            if column in (2, 3):
                paths = [(trace.path, "#df6600" if column == 2 else "#0077bb")]
            for path, colour in paths:
                axis.plot([grid.points[n][0] for n in path], [grid.points[n][1] for n in path], color=colour, lw=2)
            first, last = grid.points[home], grid.points[destination]
            centre = ((first[0] + last[0]) / 2, (first[1] + last[1]) / 2)
            axis.add_patch(Circle(centre, settings.radius, fill=False, color="#cc4477", ls="--"))
            axis.axline(first, last, color="gray", lw=0.7, ls=":")
            axis.scatter([first[0], last[0]], [first[1], last[1]], color="black", s=25, zorder=5)
            axis.set(xlim=(0, max(p[0] for p in grid.points)), ylim=(0, max(p[1] for p in grid.points)), aspect="equal")
            title = ("Outbound 25%", "Outbound 60%", "Outbound complete", "Return complete", "Closed outing")[column]
            axis.set_title(f"{title}\ncentre={settings.centre_strength:g}, side={settings.side_strength:g}")
            if column == 0:
                axis.set_ylabel("Metres")
    figure.suptitle("Random Delaunay A*: expansion order (purple → yellow), outbound orange, return blue")
    figure.savefig(output, dpi=150)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/experiments/smooth-loops"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--samples", type=int, default=8000)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    grid = random_delaunay_graph(args.samples, 100, 80, seed=args.seed, anchors=((10, 40), (90, 40)))
    home, destination = 0, 1
    (args.output_dir / "graph.json").write_text(json.dumps({
        "seed": args.seed, "sample_count": args.samples, "width": 100, "height": 80,
        "points": grid.points, "neighbours": grid.neighbours, "home": home, "destination": destination,
    }) + "\n")
    settings_list = [Settings(0, 0, 0), Settings(2), Settings(8)]
    runs: list[tuple[Settings, SearchResult, SearchResult]] = []
    records: list[Mapping[str, object]] = []
    for settings in settings_list:
        for outbound_left in (True, False):
            started = perf_counter()
            result = plan_outing(grid, home, destination, settings, outbound_left)
            elapsed = perf_counter() - started
            if result is None:
                raise RuntimeError("Connected triangulation unexpectedly has no outing")
            outbound, returning = result
            if outbound_left:
                runs.append((settings, outbound, returning))
            records.append({"graph": "random_delaunay", "seed": args.seed, "sample_count": args.samples,
                            "centre_strength": settings.centre_strength, "side_strength": settings.side_strength,
                            "reuse_strength": settings.reuse_strength, "radius": settings.radius,
                            "transition_width": settings.transition_width, "outbound_left": outbound_left,
                            "seconds_including_weights": elapsed,
                            "length": path_length(grid, outbound.path) + path_length(grid, returning.path),
                            "reused_length": sum(math.dist(grid.points[a], grid.points[b]) for a, b in
                                                 path_edges(outbound.path) & path_edges(returning.path)),
                            "outbound_expansions": len(outbound.expansions),
                            "return_expansions": len(returning.expansions),
                            "outbound_cost": outbound.cost, "return_cost": returning.cost,
                            "outbound_path": [grid.points[n] for n in outbound.path],
                            "return_path": [grid.points[n] for n in returning.path]})
    plot_experiment(grid, home, destination, runs, args.output_dir / "evolution.png")
    (args.output_dir / "results.json").write_text(json.dumps(records, indent=2) + "\n")
    print(f"Saved {args.output_dir / 'evolution.png'} and results.json")
    for record in records:
        print(f"centre={record['centre_strength']}, left={record['outbound_left']}: "
              f"length={record['length']}, reused={record['reused_length']}, "
              f"elapsed={record['seconds_including_weights']:.3f}s")


if __name__ == "__main__":
    main()

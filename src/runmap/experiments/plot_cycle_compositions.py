"""Render rooted cycle-envelope compositions in metric coordinates."""

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt

from runmap.experiments.plot_cycle_catalog_band import (
    _candidate_colors,
    _inset_line,
    _project_line,
)
from runmap.routing.search import GraphIndex, route_coordinates


def plot_compositions(graph, base, catalog, start, output, inset_fraction=0.006):
    composition = catalog.get("composition")
    if not composition or not composition["cycles"]:
        raise ValueError("catalog has no cycle compositions")
    index = GraphIndex(graph)
    cycles = composition["cycles"]
    module_counts = sorted({cycle["module_count"] for cycle in cycles})
    lines = {}
    for cycle in cycles:
        line = _project_line(route_coordinates(index, cycle["boundary_arc_ids"]))
        center = _project_line([cycle["centroid"]])[0]
        lines[cycle["id"]] = _inset_line(line, center, inset_fraction)
    eastings = [point[0] for line in lines.values() for point in line]
    northings = [point[1] for line in lines.values() for point in line]
    west, east = min(eastings), max(eastings)
    south, north = min(northings), max(northings)
    padding = max(east - west, north - south) * 0.05
    colors = dict(zip(
        sorted(lines), _candidate_colors(len(lines), seed=130363)
    ))
    figure, axes = plt.subplots(
        1, len(module_counts), figsize=(6 * len(module_counts), 6), squeeze=False
    )
    start_point = _project_line([[
        start["longitude"], start["latitude"]
    ]])[0]
    for axis, module_count in zip(axes.flat, module_counts):
        for feature in base["features"]:
            line = _project_line(feature["geometry"]["coordinates"])
            if (
                max(point[0] for point in line) < west - padding
                or min(point[0] for point in line) > east + padding
                or max(point[1] for point in line) < south - padding
                or min(point[1] for point in line) > north + padding
            ):
                continue
            axis.plot(
                *zip(*line), color="#6f7b82", linewidth=0.65,
                alpha=0.55, zorder=1,
            )
        level = [
            cycle for cycle in cycles if cycle["module_count"] == module_count
        ]
        for cycle in level:
            line = lines[cycle["id"]]
            color = colors[cycle["id"]]
            axis.plot(*zip(*line), color=color, linewidth=2, alpha=0.88, zorder=3)
            midpoint = line[len(line) // 2]
            axis.text(
                midpoint[0], midpoint[1],
                cycle["id"].removeprefix("composition-"),
                color=color, fontsize=8, fontweight="bold", zorder=4,
            )
        axis.scatter(
            *start_point, marker="*", s=100, color="black", zorder=5
        )
        distances = [cycle["metrics"]["distance_m"] for cycle in level]
        axis.set_title(
            f"{module_count} modules: {len(level)} envelopes\n"
            f"{min(distances) / 1000:.2f}–{max(distances) / 1000:.2f} km"
        )
        axis.set_xlim(west - padding, east + padding)
        axis.set_ylim(south - padding, north + padding)
        axis.set_aspect("equal", adjustable="box")
        axis.ticklabel_format(useOffset=False, style="plain")
        axis.set_xlabel("Easting (m), EPSG:3067")
        axis.grid(color="#d5dadd", linewidth=0.5, alpha=0.6)
    axes.flat[0].set_ylabel("Northing (m), EPSG:3067")
    figure.suptitle(
        f"Rooted 1 km cycle-envelope compositions "
        f"({len(composition['root_candidate_ids'])} roots)"
    )
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=180)
    plt.close(figure)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inset-percent", type=float, default=0.6)
    args = parser.parse_args(argv)
    experiment = json.loads(args.experiment.read_text())
    plot_compositions(
        json.loads(args.graph.read_text()),
        json.loads(args.base.read_text()),
        json.loads(args.catalog.read_text()),
        experiment["start"],
        args.output,
        args.inset_percent / 100,
    )


if __name__ == "__main__":
    main()

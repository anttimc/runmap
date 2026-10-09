"""Plot every generated cycle in one catalog length band in metric coordinates."""

import argparse
import colorsys
import json
from pathlib import Path
import random

import matplotlib.pyplot as plt
from rasterio.warp import transform

from runmap.routing.search import GraphIndex, route_coordinates


def _project_line(coordinates):
    longitudes, latitudes = zip(*coordinates)
    eastings, northings = transform(
        "EPSG:4326", "EPSG:3067", longitudes, latitudes
    )
    return list(zip(eastings, northings))


def _inset_line(line, center, fraction):
    """Move display geometry slightly towards its cycle centroid."""
    return [
        (
            center[0] + (point[0] - center[0]) * (1 - fraction),
            center[1] + (point[1] - center[1]) * (1 - fraction),
        )
        for point in line
    ]


def _candidate_colors(count, seed=104729):
    """Return distinct-looking colors in a stable randomized assignment."""
    rng = random.Random(seed)
    phase = rng.random()
    colors = [
        colorsys.hsv_to_rgb(
            (offset / count + phase) % 1,
            rng.uniform(0.72, 0.92),
            rng.uniform(0.68, 0.9),
        )
        for offset in range(count)
    ]
    rng.shuffle(colors)
    return colors


def plot_catalog_band(
    graph, base, catalog, start, target_m, output, inset_fraction=0.012
):
    if not 0 <= inset_fraction < 1:
        raise ValueError("display inset must be between 0% and 100%")
    index = GraphIndex(graph)
    candidates = [
        candidate
        for candidate in catalog["candidate_pool"]
        if float(candidate["target_band_m"]) == target_m
    ]
    if not candidates:
        raise ValueError(f"catalog has no candidates in the {target_m:g} m band")

    cycle_lines = []
    for candidate in candidates:
        line = _project_line(
            route_coordinates(index, candidate["boundary_arc_ids"])
        )
        center = _project_line([candidate["centroid"]])[0]
        cycle_lines.append(_inset_line(line, center, inset_fraction))
    eastings = [point[0] for line in cycle_lines for point in line]
    northings = [point[1] for line in cycle_lines for point in line]
    west, east = min(eastings), max(eastings)
    south, north = min(northings), max(northings)
    padding = max(east - west, north - south) * 0.04
    selected_ids = {
        candidate["candidate_id"] for candidate in catalog["cycles"]
    }

    figure, axis = plt.subplots(figsize=(11, 9))
    for feature in base["features"]:
        line = _project_line(feature["geometry"]["coordinates"])
        line_west = min(point[0] for point in line)
        line_east = max(point[0] for point in line)
        line_south = min(point[1] for point in line)
        line_north = max(point[1] for point in line)
        if (
            line_east < west - padding
            or line_west > east + padding
            or line_north < south - padding
            or line_south > north + padding
        ):
            continue
        axis.plot(
            *zip(*line), color="#69767e", linewidth=0.7, alpha=0.65, zorder=1
        )

    colors = _candidate_colors(len(candidates))
    for candidate, line, color in zip(candidates, cycle_lines, colors):
        selected = candidate["candidate_id"] in selected_ids
        axis.plot(
            *zip(*line),
            color=color,
            linewidth=2.2 if selected else 1.35,
            alpha=0.95 if selected else 0.72,
            zorder=3 if selected else 2,
        )
        midpoint = line[len(line) // 2]
        axis.text(
            midpoint[0],
            midpoint[1],
            candidate["candidate_id"].removeprefix("candidate-"),
            fontsize=6,
            color=color,
            zorder=4,
        )

    start_easting, start_northing = _project_line([[
        start["longitude"], start["latitude"]
    ]])[0]
    axis.scatter(
        start_easting,
        start_northing,
        marker="*",
        s=120,
        color="black",
        label=start.get("name", "start"),
        zorder=5,
    )
    axis.set_xlim(west - padding, east + padding)
    axis.set_ylim(south - padding, north + padding)
    axis.set_aspect("equal", adjustable="box")
    axis.ticklabel_format(useOffset=False, style="plain")
    axis.set_xlabel("Easting (m), EPSG:3067")
    axis.set_ylabel("Northing (m), EPSG:3067")
    axis.set_title(
        f"All {len(candidates)} generated {target_m / 1000:g} km cycle candidates\n"
        f"Thicker lines are selected; {inset_fraction * 100:g}% display inset"
    )
    axis.legend(loc="upper left")
    axis.grid(color="#d5dadd", linewidth=0.5, alpha=0.6)
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
    parser.add_argument("--target-m", type=float, required=True)
    parser.add_argument("--inset-percent", type=float, default=1.2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    experiment = json.loads(args.experiment.read_text())
    plot_catalog_band(
        json.loads(args.graph.read_text()),
        json.loads(args.base.read_text()),
        json.loads(args.catalog.read_text()),
        experiment["start"],
        args.target_m,
        args.output,
        args.inset_percent / 100,
    )


if __name__ == "__main__":
    main()

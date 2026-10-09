"""Render a multiscale cycle catalog for visual inspection."""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt


def plot_cycle_catalog(base, catalog, start, output, profile_name="running"):
    grouped = defaultdict(list)
    for feature in catalog["features"]:
        grouped[float(feature["properties"]["target_band_m"])].append(feature)
    bands = sorted(grouped)
    columns = 2
    rows = math.ceil(len(bands) / columns)
    figure, axes = plt.subplots(rows, columns, figsize=(15, 3.5 * rows), squeeze=False)
    coordinates = [
        point
        for feature in catalog["features"]
        for point in feature["geometry"]["coordinates"]
    ]
    west = min(point[0] for point in coordinates)
    east = max(point[0] for point in coordinates)
    south = min(point[1] for point in coordinates)
    north = max(point[1] for point in coordinates)
    longitude_padding = (east - west) * 0.05
    latitude_padding = (north - south) * 0.05
    colors = plt.get_cmap("tab20")
    for axis, target in zip(axes.flat, bands):
        for feature in base["features"]:
            line = feature["geometry"]["coordinates"]
            line_west = min(point[0] for point in line)
            line_east = max(point[0] for point in line)
            line_south = min(point[1] for point in line)
            line_north = max(point[1] for point in line)
            if line_east < west or line_west > east or line_north < south or line_south > north:
                continue
            axis.plot(
                *zip(*line), color="#7f8b93", linewidth=0.65, alpha=0.7, zorder=1
            )
        for offset, feature in enumerate(grouped[target]):
            line = feature["geometry"]["coordinates"]
            color = colors(offset % 20)
            axis.plot(*zip(*line), color=color, linewidth=2, alpha=0.9, zorder=3)
            midpoint = line[len(line) // 2]
            axis.text(
                midpoint[0], midpoint[1], feature["id"].removeprefix("cycle-"),
                fontsize=7, color=color, zorder=4,
            )
        axis.scatter(
            start["longitude"], start["latitude"], marker="*", s=85,
            color="black", zorder=5,
        )
        axis.set_title(f"{target / 1000:g} km cycles ({len(grouped[target])})")
        axis.set_xlim(west - longitude_padding, east + longitude_padding)
        axis.set_ylim(south - latitude_padding, north + latitude_padding)
        axis.ticklabel_format(useOffset=False)
        axis.set_aspect(0.5)
        axis.set_xlabel("longitude")
        axis.set_ylabel("latitude")
    for axis in axes.flat[len(bands):]:
        axis.set_visible(False)
    figure.suptitle(f"Preplanned {profile_name} cycle catalog")
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160)
    plt.close(figure)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    base = json.loads(args.base.read_text())
    catalog = json.loads(args.catalog.read_text())
    experiment = json.loads(args.experiment.read_text())
    profile_name = (
        experiment.get("topology", {})
        .get("face_search", {})
        .get("edge_profile", {})
        .get("id", "running")
        .replace("-", " ")
    )
    plot_cycle_catalog(
        base, catalog, experiment["start"], args.output, profile_name
    )


if __name__ == "__main__":
    main()

"""Show the active microcycle partition and where atomic repair changed it."""

import argparse
import json
from pathlib import Path
import time

from runmap.experiments.plot_atomic_regions import COLORS, _project, plot_atomic_regions
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D

from runmap.routing.search import GraphIndex


def plot_microcycle_partition(index, inventory, partition, output, repairs_output):
    started = time.perf_counter()
    plot_atomic_regions(index, partition, output)
    projected = {segment: _project(item["geometry"]) for segment, item in index.segments.items()}
    bounds = inventory.get("source_bounds_epsg3067")
    if bounds is None:
        points = [point for region in inventory["regions"] for segment in region["boundary_segment_ids"]
                  for point in projected[segment]]
        xs, ys = zip(*points)
        bounds = [min(xs), min(ys), max(xs), max(ys)]
    west, south, east, north = bounds
    padding = max(east - west, north - south, 1) * 0.02
    base = [line for line in projected.values() if max(x for x, _ in line) >= west - padding
            and min(x for x, _ in line) <= east + padding and max(y for _, y in line) >= south - padding
            and min(y for _, y in line) <= north + padding]
    figure, axes = plt.subplots(1, 3, figsize=(19, 7))
    for axis, document, title in zip(axes[:2], (inventory, partition), ("Before: atomic regions", "After: microcycle partition")):
        for status, color in COLORS.items():
            lines = [projected[segment] for region in document["regions"] if region["status"] == status
                     for segment in region["boundary_segment_ids"]]
            axis.add_collection(LineCollection(lines, colors=color, linewidths=1.3, zorder=2))
        axis.set_title(title)
    changed = [region for region in partition["regions"] if region["children"]]
    changed_atoms = {atom for region in changed for atom in region["atomic_region_ids"]}
    before = [projected[segment] for region in inventory["regions"] if region["id"] in changed_atoms
              for segment in region["boundary_segment_ids"]]
    after = [projected[segment] for region in changed for segment in region["boundary_segment_ids"]]
    axes[2].add_collection(LineCollection(before, colors="#df8b00", linewidths=1, linestyles="dotted", zorder=2))
    axes[2].add_collection(LineCollection(after, colors="#1476ba", linewidths=2, zorder=3))
    for region in changed:
        center = _project([region["centroid"]])[0]
        axes[2].text(*center, region["id"].removeprefix("microcycle-merge-"), color="#075b99", fontsize=6, zorder=4)
    axes[2].set_title(f"{len(changed)} repaired envelopes ({partition['summary']['merged_count']} merges)")
    axes[2].legend(handles=[Line2D([0], [0], color="#df8b00", linestyle=":", label="Original atomic boundaries"),
                           Line2D([0], [0], color="#1476ba", label="Repaired outer boundaries")], loc="upper left")
    node = index.nodes[partition["start_node_id"]]
    start = _project([[node["longitude"], node["latitude"]]])[0]
    for axis in axes:
        axis.add_collection(LineCollection(base, colors="#66747d", linewidths=0.7, alpha=0.65, zorder=1))
        axis.scatter(*start, marker="*", color="black", s=90, zorder=5)
        axis.set_xlim(west - padding, east + padding)
        axis.set_ylim(south - padding, north + padding)
        axis.set_aspect("equal", adjustable="box")
        axis.ticklabel_format(useOffset=False, style="plain")
        axis.set_xlabel("Easting (m), EPSG:3067")
        axis.set_ylabel("Northing (m), EPSG:3067")
    axes[0].legend(handles=[Line2D([0], [0], color=color, label=status) for status, color in COLORS.items()], loc="upper left")
    figure.suptitle("Stage 2: local repair without a requested route length; labels refer to merge IDs")
    figure.tight_layout()
    repairs_output = Path(repairs_output)
    repairs_output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(repairs_output, dpi=180)
    plt.close(figure)
    return round(time.perf_counter() - started, 6)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--partition", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repairs-output", type=Path, required=True)
    args = parser.parse_args(argv)
    plot_microcycle_partition(GraphIndex(json.loads(args.graph.read_text())),
                             json.loads(args.inventory.read_text()), json.loads(args.partition.read_text()),
                             args.output, args.repairs_output)


if __name__ == "__main__":
    main()

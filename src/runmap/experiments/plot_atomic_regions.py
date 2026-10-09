"""Render atomic classifications over a metric path map, including invalid gaps."""

import argparse
import json
from pathlib import Path
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from rasterio.warp import transform

from runmap.routing.atomic_regions import STATUSES
from runmap.routing.search import GraphIndex


COLORS = {"accepted": "#148442", "repair-needed": "#df8b00",
          "connector-only": "#744ac3", "invalid": "#d53846"}


def _project(coordinates):
    xs, ys = transform("EPSG:4326", "EPSG:3067", *zip(*coordinates))
    return list(zip(xs, ys))


def plot_atomic_regions(index, inventory, output):
    started = time.perf_counter()
    microcycles = inventory.get("kind") == "microcycle-partition"
    projected = {segment_id: _project(segment["geometry"])
                 for segment_id, segment in index.segments.items()}
    lines = {status: [] for status in STATUSES}
    for region in inventory["regions"]:
        lines[region["status"]].extend(projected[segment] for segment in region["boundary_segment_ids"])
    bounds = inventory.get("source_bounds_epsg3067")
    if bounds is None:
        points = [point for category in lines.values() for line in category for point in line]
        if not points:
            points = [point for line in projected.values() for point in line]
        xs, ys = zip(*points)
        bounds = [min(xs), min(ys), max(xs), max(ys)]
    west, south, east, north = bounds
    padding = max(east - west, north - south, 1) * 0.02
    base_lines = [line for line in projected.values()
                  if max(point[0] for point in line) >= west - padding
                  and min(point[0] for point in line) <= east + padding
                  and max(point[1] for point in line) >= south - padding
                  and min(point[1] for point in line) <= north + padding]
    node = index.nodes[inventory["start_node_id"]]
    start = _project([[node["longitude"], node["latitude"]]])[0]
    counts = inventory["summary"]["classification_counts"]
    figure, axes = plt.subplots(2, 3, figsize=(19, 12))
    for axis, category in zip(axes.flat, (None, *STATUSES)):
        axis.add_collection(LineCollection(base_lines, colors="#66747d", linewidths=0.7, alpha=0.7, zorder=1))
        for status in STATUSES if category is None else (category,):
            axis.add_collection(LineCollection(lines[status], colors=COLORS[status], linewidths=1.7, alpha=0.9, zorder=2))
        axis.scatter(*start, marker="*", s=100, color="black", zorder=4)
        axis.set_xlim(west - padding, east + padding)
        axis.set_ylim(south - padding, north + padding)
        axis.set_aspect("equal", adjustable="box")
        axis.ticklabel_format(useOffset=False, style="plain")
        axis.set_xlabel("Easting (m), EPSG:3067")
        axis.set_ylabel("Northing (m), EPSG:3067")
        axis.grid(color="#d5dadd", linewidth=0.5, alpha=0.5)
        axis.set_title(("Every active microcycle boundary" if microcycles else "Every atomic boundary")
                       if category is None else f"{category}: {counts[category]}")
    axes.flat[0].legend(handles=[Line2D([0], [0], color=COLORS[status], label=status) for status in STATUSES], loc="upper left")
    diagnostic = axes.flat[-1]
    diagnostic.axis("off")
    summary = inventory["summary"]
    text = [f"{summary['base_face_count']} retained base faces, each owned exactly once",
            f"{summary['atomic_region_count']} atomic regions",
            f"{summary.get('filled_hole_count', 0)} enclosed holes filled with retained faces",
            f"{summary['shared_interface_count']} symmetric runnable interfaces",
            f"{summary['unreachable_count']} regions without round-trip reachability", "", "Rejection / shape reasons:"]
    if microcycles:
        text[2] = f"{summary['active_region_count']} active regions, {summary['merged_count']} repair merges"
        text.insert(3, f"{summary['repaired_count']} repaired microcycles; children retained in the tree")
    text.extend(f"  {reason}: {count}" for reason, count in inventory["rejection_summary"].items())
    source = inventory.get("source_topology_summary", {})
    text.extend(["", f"Base topology: {source.get('face_test_segment_count', '?')} face-test segments",
                 "Gray paths are internal to regions or outside retained faces.",
                 "Red boundaries are retained invalid regions.",
                 "Purple regions are available for connections and later merges.",
                 "Fully unknown boundaries are excluded from standalone loops.",
                 "Black star: snapped home vertex."])
    diagnostic.text(0, 0.98, "\n".join(text), va="top", fontsize=10, transform=diagnostic.transAxes)
    stage = "Disjoint microcycle partition" if microcycles else "Atomic region inventory"
    figure.suptitle(f"{stage} — {inventory['effective_edge_profile'].get('id', 'running')}")
    figure.tight_layout()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=180)
    plt.close(figure)
    return round(time.perf_counter() - started, 6)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    plot_atomic_regions(GraphIndex(json.loads(args.graph.read_text())),
                        json.loads(args.inventory.read_text()), args.output)


if __name__ == "__main__":
    main()

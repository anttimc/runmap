"""Plot saved loop-grid samples without rerunning routing or interpolating failures."""

import argparse
from collections import Counter
import csv
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.colors import BoundaryNorm, ListedColormap, Normalize
import numpy as np
from numpy.typing import NDArray

from runmap.experiments.loop_length_grid import GridSample, iter_samples
from runmap.routing.geometry import projected_points


@dataclass(frozen=True)
class Observation:
    sample_id: int
    forward_index: int
    lateral_index: int
    width_index: int
    forward_m: float
    lateral_m: float
    width: float
    status: str
    side: str
    distance_m: float | None
    outbound_m: float | None
    returning_m: float | None
    cost: float | None
    repeated_m: float | None
    route_sha256: str | None
    snap_offset_m: float | None
    snapped_longitude: float | None
    snapped_latitude: float | None
    wall_ms: float
    retries: int | None
    area_limited_reason: str | None
    source_coverage_limited: bool | None
    error: str | None


def observations(sample: GridSample) -> list[Observation]:
    point, response = sample.point, sample.response
    result: list[Observation] = []
    snap = None if response is None else response.turnaround
    search = None if response is None else response.search
    candidates = [] if response is None else response.candidates
    for candidate in candidates or [None]:
        route = None if candidate is None else [[item.model_dump() for item in leg.traversals] for leg in candidate.legs]
        result.append(Observation(point.sample_id, point.forward_index, point.lateral_index, point.width_index,
            point.forward_m, point.lateral_m, point.width, "planner_error" if response is None else response.status,
            "none" if candidate is None else ("left" if candidate.outbound_left else "right"),
            None if candidate is None else candidate.distance_m,
            None if candidate is None else candidate.legs[0].distance_m,
            None if candidate is None else candidate.legs[1].distance_m,
            None if candidate is None else sum(leg.cost for leg in candidate.legs),
            None if candidate is None else candidate.repeated_distance_m,
            None if route is None else hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest(),
            None if snap is None else snap.offset_m, None if snap is None else snap.longitude,
            None if snap is None else snap.latitude, sample.wall_s * 1000,
            None if search is None else search.retries, None if search is None else search.area_limited_reason,
            None if search is None else any(attempt.source_coverage_limited for attempt in search.attempts), sample.error))
    return result


def length_cube(rows: list[Observation], side: str) -> NDArray[np.float64]:
    """At zero width the single candidate supplies the common baseline for both sides."""
    shape = (max(row.width_index for row in rows) + 1, max(row.forward_index for row in rows) + 1,
             max(row.lateral_index for row in rows) + 1)
    cube = np.full(shape, np.nan)
    for row in rows:
        if row.status == "complete" and row.distance_m is not None and (row.side == side or row.width == 0):
            cube[row.width_index, row.forward_index, row.lateral_index] = row.distance_m
    return cube


def save_figure(output: Path, name: str) -> None:
    plt.savefig(output / f"{name}.png", dpi=160)
    plt.savefig(output / f"{name}.svg")
    plt.close()


def plot_lengths(output: Path, rows: list[Observation], forward: list[float], lateral: list[float],
                 widths: list[float]) -> None:
    cubes = [length_cube(rows, side) / 1000 for side in ("left", "right")]
    valid = np.concatenate([cube[np.isfinite(cube)] for cube in cubes])
    norm = Normalize(float(valid.min()), float(valid.max())) if len(valid) else Normalize(0, 1)
    fig, axes = plt.subplots(len(widths), 2, figsize=(11, 3 * len(widths)), squeeze=False, layout="constrained")
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad("#e5e5e5")
    for k, width in enumerate(widths):
        for side, cube, ax in zip(("left", "right"), cubes, axes[k]):
            image = ax.pcolormesh(lateral, forward, np.ma.masked_invalid(cube[k]), shading="nearest", cmap=cmap, norm=norm)
            ax.set(title=f"Width {width:.2f} · outbound {side}", xlabel="Lateral offset, positive left (m)", ylabel="Forward distance (m)", aspect="equal")
    fig.colorbar(image, ax=axes.ravel().tolist(), label="Physical loop length (km)", shrink=.6)
    fig.suptitle("Loop length over turnaround position and width\nGrey = no complete result; zero-width candidate shared by both columns")
    save_figure(output, "length-surfaces")


def plot_outcomes(output: Path, rows: list[Observation], forward: list[float], lateral: list[float],
                  widths: list[float]) -> None:
    statuses = ("complete", "no_eligible_snap", "unreachable", "deadline", "area_limited", "planner_error")
    cmap = ListedColormap(("#4daf4a", "#cccccc", "#984ea3", "#ff7f00", "#e41a1c", "#377eb8"))
    norm = BoundaryNorm(np.arange(-.5, len(statuses)), len(statuses))
    fig, axes = plt.subplots(math.ceil(len(widths) / 2), 2, figsize=(11, 4 * math.ceil(len(widths) / 2)),
                             squeeze=False, layout="constrained")
    for k, ax in enumerate(axes.ravel()):
        if k >= len(widths):
            ax.set_visible(False)
            continue
        values = np.full((len(forward), len(lateral)), np.nan)
        for row in rows:
            if row.width_index == k:
                values[row.forward_index, row.lateral_index] = statuses.index(row.status)
        image = ax.pcolormesh(lateral, forward, values, shading="nearest", cmap=cmap, norm=norm)
        ax.set(title=f"Width {widths[k]:.2f}", xlabel="Lateral offset (m)", ylabel="Forward distance (m)", aspect="equal")
    bar = fig.colorbar(image, ax=axes.ravel().tolist(), ticks=range(len(statuses)), shrink=.6)
    bar.ax.set_yticklabels(statuses)
    fig.suptitle("Planner outcomes at requested turnaround positions")
    save_figure(output, "outcomes")


def selected_positions(rows: list[Observation], forward: list[float], lateral: list[float],
                       widths: list[float]) -> list[tuple[int, int]]:
    counts = Counter((row.forward_index, row.lateral_index) for row in rows
                     if row.status == "complete" and row.side == "left")
    available = [key for key, count in counts.items() if count == len(widths)]
    if not available:
        available = list(counts)
    selected: list[tuple[int, int]] = []
    for i in (0, len(forward) // 2, len(forward) - 1):
        for j in (len(lateral) // 3, 2 * len(lateral) // 3):
            if available:
                key = min(available, key=lambda key: (key[0] - i) ** 2 + (key[1] - j) ** 2)
                selected.append(key)
                available.remove(key)
    # Include a non-monotone sweep when available, rather than hiding it in a map.
    drops = np.fmin(np.diff(length_cube(rows, "left"), axis=0),
                    np.diff(length_cube(rows, "right"), axis=0))
    if drops.size and np.any(np.isfinite(drops)) and float(np.nanmin(drops)) < -1:
        _, i, j = np.unravel_index(np.nanargmin(drops), drops.shape)
        key = (int(i), int(j))
        if key not in selected:
            selected[-1:] = [key]
    return selected


def plot_widths(output: Path, rows: list[Observation], forward: list[float], lateral: list[float],
                widths: list[float], positions: list[tuple[int, int]]) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(14, 8), layout="constrained")
    for ax, position in zip(axes.ravel(), positions):
        i, j = position
        for side, style in (("left", "o-"), ("right", "s--")):
            ax.plot(widths, length_cube(rows, side)[:, i, j] / 1000, style, label=f"Outbound {side}")
        ax.set(title=f"Forward {forward[i]:.0f} m; lateral {lateral[j]:.0f} m",
               xlabel="Width setting", ylabel="Loop length (km)")
        ax.grid(alpha=.25)
        ax.legend()
    for ax in axes.ravel()[len(positions):]:
        ax.set_visible(False)
    fig.suptitle("Width sweeps at fixed requested turnaround positions\nLines connect sampled widths; gaps indicate missing complete results")
    save_figure(output, "width-sweeps")


def plot_route(ax: Axes, sample: GridSample, bearing_deg: float, home: tuple[float, float]) -> None:
    if sample.response is None:
        ax.text(.5, .5, "Planner error", ha="center", transform=ax.transAxes)
        return
    angle = math.radians(bearing_deg)
    hx, hy = projected_points([home])[0]
    for candidate in sample.response.candidates:
        side = "left" if candidate.outbound_left else "right"
        for k, leg in enumerate(candidate.legs):
            points = projected_points(leg.coordinates)
            forward = [(x - hx) * math.sin(angle) + (y - hy) * math.cos(angle) for x, y in points]
            lateral = [-(x - hx) * math.cos(angle) + (y - hy) * math.sin(angle) for x, y in points]
            label = f"{side}: {candidate.distance_m / 1000:.2f} km" if k == 0 else None
            ax.plot(lateral, forward, color="tab:blue" if candidate.outbound_left else "tab:orange",
                    linestyle="-" if k == 0 else "--", linewidth=1.3, label=label, alpha=.8)
    ax.scatter([0], [0], marker="*", s=70, color="black", label="Start")
    ax.scatter([sample.point.lateral_m], [sample.point.forward_m], marker="x", color="red", label="Requested turn")
    ax.set(xlabel="Lateral offset (m)", ylabel="Forward distance (m)", aspect="equal")
    ax.legend(fontsize=7)
    ax.grid(alpha=.2)


def plot_examples(output: Path, positions: list[tuple[int, int]], widths: list[float],
                  bearing_deg: float, home: tuple[float, float]) -> None:
    if not positions:
        return
    # Compare the default-nearest width and the widest at the same three positions.
    width_indices = sorted({min(range(len(widths)), key=lambda k: abs(widths[k] - .18)), len(widths) - 1})
    selected = positions[::2][:3]
    fig, axes = plt.subplots(len(selected), len(width_indices), figsize=(12, 4 * len(selected)),
                             squeeze=False, layout="constrained")
    for sample in iter_samples(output / "samples.jsonl.gz"):
        point = sample.point
        key = (point.forward_index, point.lateral_index)
        if key in selected and point.width_index in width_indices:
            ax = axes[selected.index(key), width_indices.index(point.width_index)]
            plot_route(ax, sample, bearing_deg, home)
            ax.set_title(f"Width {point.width:.2f}; forward {point.forward_m:.0f}, lateral {point.lateral_m:.0f} m")
    # Share each pair's limits so width effects are visually comparable.
    for pair in axes:
        xlim = (min(ax.get_xlim()[0] for ax in pair), max(ax.get_xlim()[1] for ax in pair))
        ylim = (min(ax.get_ylim()[0] for ax in pair), max(ax.get_ylim()[1] for ax in pair))
        for ax in pair:
            ax.set(xlim=xlim, ylim=ylim)
    fig.suptitle("Selected route geometry · solid outbound, dashed return\nMetric coordinates; no basemap or implied traversability between paths")
    save_figure(output, "route-examples")


def write_summary(output: Path, rows: list[Observation]) -> None:
    samples = {row.sample_id: row for row in rows}
    complete = [row for row in rows if row.status == "complete" and row.distance_m is not None]
    distances = [row.distance_m for row in complete if row.distance_m is not None]
    fingerprints = {row.route_sha256 for row in complete}
    complete_positions = {(row.forward_index, row.lateral_index) for row in complete}
    complete_widths = Counter((row.forward_index, row.lateral_index) for row in samples.values()
                              if row.status == "complete")
    width_count = len({row.width_index for row in rows})
    jumps: list[float] = []
    decreases = comparisons = 0
    for side in ("left", "right"):
        cube = length_cube(rows, side)
        for axis in (1, 2):
            diff = np.abs(np.diff(cube, axis=axis))
            jumps.extend(diff[np.isfinite(diff)].tolist())
        diff = np.diff(cube, axis=0)
        comparisons += int(np.isfinite(diff).sum())
        decreases += int((diff < -1).sum())
    summary = {"evaluations": len(samples), "complete_candidates": len(complete),
        "spatial_positions": len({(row.forward_index, row.lateral_index) for row in rows}),
        "positions_with_complete_result": len(complete_positions),
        "positions_complete_at_every_width": sum(count == width_count for count in complete_widths.values()),
        "statuses": dict(Counter(row.status for row in samples.values())),
        "unique_ordered_route_fingerprints": len(fingerprints),
        "distance_min_m": min(distances) if distances else None,
        "distance_max_m": max(distances) if distances else None,
        "spatial_neighbor_length_jump_p50_m": float(np.percentile(jumps, 50)) if jumps else None,
        "spatial_neighbor_length_jump_p95_m": float(np.percentile(jumps, 95)) if jumps else None,
        "width_adjacent_comparisons": comparisons, "width_decreases_over_1m": decreases,
        "evaluation_p50_ms": float(np.percentile([row.wall_ms for row in samples.values()], 50)),
        "evaluation_p95_ms": float(np.percentile([row.wall_ms for row in samples.values()], 95)),
        "coverage_limited_evaluations": sum(row.source_coverage_limited is True for row in samples.values()),
        "planner_errors": dict(Counter(row.error for row in samples.values() if row.error is not None))}
    summary["plot_implementation_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    with (output / "observations.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[field.name for field in fields(Observation)])
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def plot_experiment(output: Path) -> None:
    manifest = json.loads((output / "manifest.json").read_text())
    if not manifest["complete"]:
        raise ValueError("Sampling is incomplete; do not interpret it as a complete parameter grid")
    rows = [row for sample in iter_samples(output / "samples.jsonl.gz") for row in observations(sample)]
    if not rows:
        raise ValueError("No samples to plot")
    expected = manifest["sample_count"]
    if {row.sample_id for row in rows} != set(range(expected)):
        raise ValueError("Sampling is incomplete; do not interpret it as a complete parameter grid")
    forward = sorted({row.forward_m for row in rows})
    lateral = sorted({row.lateral_m for row in rows})
    widths = [width for _, width in sorted({(row.width_index, row.width) for row in rows})]
    positions = selected_positions(rows, forward, lateral, widths)
    write_summary(output, rows)
    plot_lengths(output, rows, forward, lateral, widths)
    plot_outcomes(output, rows, forward, lateral, widths)
    plot_widths(output, rows, forward, lateral, widths, positions)
    config = manifest["config"]
    plot_examples(output, positions, widths, config["bearing_deg"], tuple(config["home"]))
    print(f"Plots and observations written to {output}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Directory containing manifest.json and samples.jsonl.gz")
    plot_experiment(parser.parse_args().output)


if __name__ == "__main__":
    main()

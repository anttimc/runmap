"""Summarize matched turnaround cohorts and plot measured width response."""

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import csv
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from numpy.typing import NDArray

from runmap.experiments.loop_width_sweep import SweepSample, read_samples
from runmap.routing.geometry import projected_points
from runmap.routing.turnaround import ellipse_extra_ratio


@dataclass(frozen=True)
class WidthRow:
    start: str
    radius_m: float
    bearing_deg: float
    width: float
    separation_m: float
    baseline_m: float
    mean_length_m: float
    side_gap_m: float
    estimate_m: float
    error_pct: float


def matched_rows(samples: list[SweepSample], widths: tuple[float, ...]
                 ) -> tuple[list[WidthRow], dict[str, int]]:
    groups: dict[tuple[str, float, float], list[SweepSample]] = defaultdict(list)
    for sample in samples:
        point = sample.point
        groups[(point.start, point.radius_m, point.bearing_deg)].append(sample)
    rows: list[WidthRow] = []
    excluded: Counter[str] = Counter()
    for group in groups.values():
        if len(group) != len(widths) or {sample.point.width for sample in group} != set(widths):
            raise ValueError("Incomplete or duplicate width sweep")
        if any(sample.response is None or sample.response.status != "complete" for sample in group):
            excluded["incomplete_or_failed_position"] += 1
            continue
        responses = [sample.response for sample in group if sample.response is not None]
        if any(len(response.candidates) != (1 if sample.point.width == 0 else 2)
               for sample, response in zip(group, responses)):
            excluded["missing_side_position"] += 1
            continue
        if any(response.home != responses[0].home or response.turnaround != responses[0].turnaround
               for response in responses):
            raise ValueError("Endpoint snapping changed across widths")
        baseline = next(response.candidates[0].distance_m for sample, response in zip(group, responses)
                        if sample.point.width == 0)
        home, turnaround = responses[0].home, responses[0].turnaround
        assert home is not None and turnaround is not None
        xy = projected_points([[home.longitude, home.latitude], [turnaround.longitude, turnaround.latitude]])
        separation = float(np.linalg.norm(np.asarray(xy[1]) - np.asarray(xy[0])))
        if baseline <= 0:
            excluded["zero_length_position"] += 1
            continue
        for sample, response in zip(group, responses):
            lengths = [candidate.distance_m for candidate in response.candidates]
            mean = float(np.mean(lengths))
            estimate = baseline + separation * ellipse_extra_ratio(sample.point.width)
            rows.append(WidthRow(sample.point.start, sample.point.radius_m, sample.point.bearing_deg,
                sample.point.width, separation, baseline, mean, max(lengths) - min(lengths),
                estimate, 100 * (mean / estimate - 1)))
    return rows, dict(excluded)


def distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        raise ValueError("Cannot summarize an empty cohort")
    return {"n": len(values), "mean": float(np.mean(values)), "std": float(np.std(values, ddof=0)),
            "p10": float(np.percentile(values, 10)), "median": float(np.median(values)),
            "p90": float(np.percentile(values, 90)), "min": min(values), "max": max(values)}


def summarize(rows: list[WidthRow], radii: list[float], widths: list[float], starts: list[str]
              ) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    for start in ["all", *starts]:
        for radius in radii:
            for width in widths:
                selected = [row for row in rows if row.radius_m == radius and row.width == width
                            and (start == "all" or row.start == start)]
                if not selected:
                    continue
                summary.append({"start": start, "radius_m": radius, "width": width,
                    "length_m": distribution([row.mean_length_m for row in selected]),
                    "estimate_m": distribution([row.estimate_m for row in selected]),
                    "error_pct": distribution([row.error_pct for row in selected]),
                    "side_gap_m": distribution([row.side_gap_m for row in selected])})
    return summary


def series(rows: list[WidthRow], widths: list[float], field: str) -> NDArray[np.float64]:
    values = []
    for width in widths:
        selected = [getattr(row, field) for row in rows if row.width == width]
        if not selected:
            values.append([np.nan] * 4)
        else:
            values.append([float(np.mean(selected)), *np.percentile(selected, [10, 90]), float(np.std(selected))])
    return np.asarray(values)


def save_plot(output: Path, name: str) -> None:
    plt.savefig(output / f"{name}.png", dpi=170)
    plt.savefig(output / f"{name}.svg")
    plt.close()


def plot_pooled(output: Path, rows: list[WidthRow], widths: list[float], radii: list[float],
                starts: list[str]) -> None:
    columns = min(2, len(radii))
    fig, axes = plt.subplots((len(radii) + columns - 1) // columns, columns,
                             figsize=(13, 5 * ((len(radii) + columns - 1) // columns)),
                             squeeze=False, layout="constrained")
    x = np.asarray(widths) * 100
    for radius, ax in zip(radii, axes.ravel()):
        selected = [row for row in rows if row.radius_m == radius]
        actual = series(selected, widths, "mean_length_m") / 1000
        estimated = series(selected, widths, "estimate_m") / 1000
        ax.fill_between(x, actual[:, 1], actual[:, 2], color="#64748b", alpha=.18, label="10th–90th percentile")
        for start in starts:
            local = series([row for row in selected if row.start == start], widths, "mean_length_m") / 1000
            ax.plot(x, local[:, 0], linewidth=1.3, alpha=.8, label=start)
        ax.plot(x, actual[:, 0], "o-", color="#152d35", linewidth=2.8, markersize=4, label="Pooled mean")
        ax.plot(x, estimated[:, 0], "--", color="#bd5f11", linewidth=2.8, label="Ellipse estimate mean")
        count = sum(row.width == 0 for row in selected)
        ax.set(title=f"Turnaround {radius/1000:g} km from start · {count} matched positions",
               xlabel="Loop width setting (%)", ylabel="Physical loop length (km)")
        ax.grid(alpha=.2)
    axes.ravel()[len(radii) - 1].legend(fontsize=8, loc="upper left")
    for ax in axes.ravel()[len(radii):]:
        ax.axis("off")
    fig.suptitle("Loop length versus width — five starts\nBoth orientations averaged per turnaround; shaded spread is across turnarounds, not confidence in the mean", fontsize=12)
    save_plot(output, "width-mean-spread")


def plot_starts(output: Path, rows: list[WidthRow], widths: list[float], radii: list[float],
                starts: list[str]) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(14, 8), layout="constrained")
    x = np.asarray(widths) * 100
    for start, ax in zip(starts, axes.ravel()):
        for radius_index, radius in enumerate(radii):
            color = plt.get_cmap("tab10")(radius_index)
            selected = [row for row in rows if row.start == start and row.radius_m == radius]
            actual = series(selected, widths, "mean_length_m") / 1000
            estimated = series(selected, widths, "estimate_m") / 1000
            ax.fill_between(x, actual[:, 1], actual[:, 2], color=color, alpha=.16)
            ax.plot(x, actual[:, 0], "o-", color=color, markersize=3, label=f"{radius/1000:g} km radius: actual mean")
            ax.plot(x, estimated[:, 0], "--", color=color, label=f"{radius/1000:g} km radius: ellipse mean")
        counts = [sum(row.start == start and row.radius_m == radius and row.width == 0 for row in rows)
                  for radius in radii]
        ax.set(title=f"{start} · n={' / '.join(map(str, counts))}",
               xlabel="Loop width setting (%)", ylabel="Loop length (km)")
        ax.grid(alpha=.2)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    axes.ravel()[-1].axis("off")
    axes.ravel()[-1].legend(handles, labels, loc="upper left", bbox_to_anchor=(0, .98), fontsize=8.5, frameon=False)
    axes.ravel()[-1].text(.05, .02, "Shading: 10th–90th percentile across bearings.\nn = matched positions at each listed radius.\nSame successful positions at every width.\nHills off; all surfaces; stairs avoided.\nDashed lines are estimates, not actual routes.", transform=axes.ravel()[-1].transAxes, fontsize=8.5, va="bottom")
    fig.suptitle("Mean and variation by start — fixed turnaround radii", fontsize=14)
    save_plot(output, "width-by-start")


def plot_bias(output: Path, rows: list[WidthRow], widths: list[float], radii: list[float],
              starts: list[str]) -> None:
    columns = min(2, len(radii))
    fig, axes = plt.subplots((len(radii) + columns - 1) // columns, columns,
                             figsize=(13, 4.7 * ((len(radii) + columns - 1) // columns)),
                             squeeze=False, layout="constrained")
    x = np.asarray(widths) * 100
    for radius, ax in zip(radii, axes.ravel()):
        selected = [row for row in rows if row.radius_m == radius]
        values = series(selected, widths, "error_pct")
        ax.fill_between(x, values[:, 1], values[:, 2], color="#64748b", alpha=.18, label="Pooled 10th–90th percentile")
        for start in starts:
            local = series([row for row in selected if row.start == start], widths, "error_pct")
            ax.plot(x, local[:, 0], "o-", markersize=3, label=start)
        ax.axhline(0, color="#152d35", linewidth=1)
        ax.set(title=f"{radius/1000:g} km turnaround radius", xlabel="Loop width setting (%)",
               ylabel="Actual / ellipse estimate − 1 (%)")
        ax.grid(alpha=.2)
    axes.ravel()[len(radii) - 1].legend(fontsize=8)
    for ax in axes.ravel()[len(radii):]:
        ax.axis("off")
    fig.suptitle("Positive error means the estimated band would tend to suggest turnarounds too far away", fontsize=12)
    save_plot(output, "width-estimate-bias")


def analyze_sweep(output: Path) -> None:
    manifest = json.loads((output / "manifest.json").read_text())
    if not manifest["complete"]:
        raise ValueError("Sampling must finish before cohort analysis")
    config = manifest["config"]
    widths, radii = config["widths"], config["radii_m"]
    starts = [start["name"] for start in config["starts"]]
    samples = list(read_samples(output / "samples.jsonl.gz", retain_geometry=False))
    if hashlib.sha256((output / "samples.jsonl.gz").read_bytes()).hexdigest() != manifest["samples_sha256"]:
        raise ValueError("Raw sample digest changed")
    if len(samples) != manifest["evaluations"]:
        raise ValueError("Raw sample count disagrees with the manifest")
    rows, excluded = matched_rows(samples, tuple(widths))
    if not rows:
        raise ValueError("No positions completed at every width")
    statuses: dict[str, Counter[str]] = defaultdict(Counter)
    for sample in samples:
        statuses[sample.point.start]["planner_error" if sample.response is None else sample.response.status] += 1
    with (output / "matched.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    report = {"manifest": manifest, "excluded_positions": excluded,
        "retained_positions": sum(row.width == 0 for row in rows),
        "retained_evaluations": len(rows),
        "retained_candidate_loops": sum(1 if row.width == 0 else 2 for row in rows),
        "planner_errors": dict(Counter(sample.error for sample in samples if sample.error is not None)),
        "area_limit_reasons": dict(Counter(sample.response.search.area_limited_reason for sample in samples
            if sample.response is not None and sample.response.status == "area_limited" and sample.response.search is not None)),
        "complete_with_source_coverage_warning": sum(sample.response is not None and sample.response.status == "complete"
            and sample.response.search is not None and any(attempt.source_coverage_limited
                for attempt in sample.response.search.attempts) for sample in samples),
        "outcomes_by_width": {str(width): dict(Counter("planner_error" if sample.response is None
            else sample.response.status for sample in samples if sample.point.width == width)) for width in widths},
        "analysis_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "statuses_by_start": {name: dict(counts) for name, counts in statuses.items()},
        "statistics_contract": "Average orientations per position first; fixed all-width complete cohorts. Pooled statistics weight each retained position equally. Population SD and empirical quantiles describe across-position spread, not confidence intervals.",
        "model": "baseline=zero-width physical length with hills off; estimate=baseline+snapped separation*ellipse_extra_ratio(width)",
        "summary": summarize(rows, radii, widths, starts)}
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    plot_pooled(output, rows, widths, radii, starts)
    plot_starts(output, rows, widths, radii, starts)
    plot_bias(output, rows, widths, radii, starts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    analyze_sweep(parser.parse_args().output)


if __name__ == "__main__":
    main()

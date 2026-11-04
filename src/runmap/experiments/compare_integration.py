"""Compare native quadrature speed and accuracy on real Helsinki segment geometry."""

import argparse
from contextlib import closing
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import subprocess

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from runmap.experiments.benchmark_native_routing import DEFAULT_BOUNDS
from runmap.paths.database import network_metadata
from runmap.paths.numeric_database import iter_numeric_chunks, open_numeric_database

Point = tuple[float, float]
Geometry = tuple[Point, ...]


@dataclass(frozen=True)
class Scenario:
    separation_m: float
    width_ratio: float
    radius_m: float
    width_m: float
    home: Point
    turnaround: Point


def load_geometries(database: Path, count: int) -> tuple[list[Geometry], str, int]:
    geometries: dict[int, Geometry] = {}
    west, south, east, north = DEFAULT_BOUNDS
    with closing(open_numeric_database(database)) as connection:
        identity = network_metadata(connection).dataset_id
        for stored in iter_numeric_chunks(connection, DEFAULT_BOUNDS):
            arrays = stored.chunk.arrays
            offsets = arrays["geometry_offsets"]
            xy = arrays["geometry_xy"]
            for index, segment_id in enumerate(arrays["segment_ids"]):
                points = tuple((float(p[0]), float(p[1])) for p in xy[int(offsets[index]):int(offsets[index + 1])])
                if len(points) >= 2 and any(west <= x <= east and south <= y <= north for x, y in points):
                    geometries[int(segment_id)] = points
    ordered = [geometries[key] for key in sorted(geometries)]
    return random.Random(20261102).sample(ordered, min(count, len(ordered))), identity, len(ordered)


def make_scenarios(geometries: list[Geometry]) -> list[Scenario]:
    centre = np.median(np.array([points[len(points) // 2] for points in geometries]), axis=0)
    return [Scenario(distance, ratio, distance * .15, distance * ratio,
                     (float(centre[0] - distance / 2), float(centre[1])),
                     (float(centre[0] + distance / 2), float(centre[1])))
            for distance in (500.0, 1500.0, 3000.0) for ratio in (.01, .05, .15)]


def write_cases(path: Path, geometries: list[Geometry], scenarios: list[Scenario]) -> None:
    with path.open("w") as stream:
        stream.write(f"{len(scenarios)}\n")
        for scenario in scenarios:
            stream.write(f"{len(geometries)}\n")
            for points in geometries:
                values = (*scenario.home, *scenario.turnaround, scenario.radius_m, scenario.width_m,
                          len(points), *(coordinate for point in points for coordinate in point))
                stream.write(" ".join(map(str, values)) + "\n")


def render(output: Path, rows: list[dict[str, float]], scenarios: list[Scenario]) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(17, 5), layout="constrained")
    labels = [f"{int(d)} m / {int(d*r)} m" for d in (500, 1500, 3000) for r in (.01, .05, .15)]
    for minimum, marker in ((4, "o"), (2, "s"), (1, "^")):
        subset = [row for row in rows if row["minimum"] == minimum]
        for cap, colour in zip((5, 10, 20, 40), ("#2864a8", "#199a83", "#de992b", "#bc4775")):
            values = [row for row in subset if row["cap_m"] == cap]
            baselines = [next(row for row in rows if row["scenario"] == i and row["cap_m"] == 5 and row["minimum"] == 4) for i in range(len(scenarios))]
            speedups = [base["median_ms"] / row["median_ms"] for base, row in zip(baselines, values)]
            errors = [max(row["error_max_pct"], 1e-9) for row in values]
            axes[0].scatter(speedups, errors, c=colour, marker=marker, label=f"{cap} m / min {minimum}", alpha=.7)
            axes[1].plot(range(len(values)), speedups, marker=marker, color=colour, alpha=.65)
    axes[0].set(xlabel="Integration speedup vs previous 5 m / 4", ylabel="Worst integral error (% of segment length)", yscale="log", title="Speed–accuracy tradeoff (nine scenarios)")
    axes[0].legend(fontsize=7, ncol=2)
    axes[1].set(xticks=range(len(labels)), xticklabels=labels, ylabel="Integration speedup vs previous 5 m / 4", title="Separation / transition width")
    axes[1].tick_params(axis="x", rotation=65)
    axes[1].axhline(1, color="grey", linewidth=1)
    baseline = [row for row in rows if row["cap_m"] == 5 and row["minimum"] == 4]
    floor = [100 * row["floor_pieces"] / row["pieces"] for row in baseline]
    axes[2].bar(range(len(labels)), floor, color="#2864a8")
    axes[2].set(xticks=range(len(labels)), xticklabels=labels, ylabel="Geometry pieces (%)", ylim=(0, 100), title="Previous 5 m / 4 pieces at sample minimum")
    axes[2].tick_params(axis="x", rotation=65)
    fig.suptitle("Native midpoint integration • real geometry • routing defaults unchanged", fontsize=14)
    fig.savefig(output / "overview.png", dpi=170)
    fig.savefig(output / "overview.svg")
    plt.close(fig)
    table = "".join("<tr>" + "".join(f"<td>{row[key]:.5g}</td>" for key in rows[0]) + "</tr>" for row in rows)
    head = "".join(f"<th>{key}</th>" for key in rows[0])
    (output / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Integration comparison</title>'
        '<style>body{font:15px system-ui;margin:32px}svg{width:100%;height:auto}td,th{padding:5px;text-align:right}table{border-collapse:collapse}tr:nth-child(even){background:#eee}</style>'
        '<h1>Native integration comparison</h1><p>Caps 5/10/20/40 m × minimums 4/2/1. '
        'Spacing also remains ≤ min(radius, width)/4. Reference: 0.05 m; convergence check: 0.1 m. '
        'Error is max absolute radial/side integral error divided by polyline length, expressed as a percentage. '
        'Timing includes validation, frame preparation and result collection, excludes decoding/clipping/Python conversion. '
        'These are complete physical segments, uniformly sampled from the fixed area; they are not weighted by search visits. '
        'Scenario IDs 0–8 correspond to the chart labels. Routes were not recomputed.</p>'
        + (output / "overview.svg").read_text()
        + '<p><a href="results.csv">CSV</a> · <a href="metadata.json">Dataset and method</a></p>'
        + '<table><thead><tr>' + head + '</tr></thead><tbody>' + table + '</tbody></table>')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--count", type=int, default=3000)
    parser.add_argument("--repeats", type=int, default=21)
    parser.add_argument("--output", type=Path, default=Path("artifacts/integration-comparison-2026-11-02"))
    args = parser.parse_args()
    if args.count < 1 or args.repeats < 1:
        parser.error("count and repeats must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    geometries, identity, population = load_geometries(args.database, args.count)
    scenarios = make_scenarios(geometries)
    cases = args.output / "cases.txt"
    write_cases(cases, geometries, scenarios)
    executable = Path("rust/target/release/examples/compare_integration")
    result = subprocess.run([str(executable.resolve()), str(cases), str(args.repeats)], check=True, capture_output=True, text=True)
    (args.output / "results.csv").write_text(result.stdout)
    rows = [{key: float(value) for key, value in row.items()} for row in csv.DictReader(result.stdout.splitlines())]
    lengths = [math.dist(a, b) for points in geometries for a, b in zip(points, points[1:])]
    metadata = dict(created_utc=datetime.now(timezone.utc).isoformat(), platform=platform.platform(),
                    revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    dataset_id=identity, database=str(args.database), bounds=DEFAULT_BOUNDS,
                    geometries=len(geometries), eligible_population=population, repeats=args.repeats, batches_per_sample=10,
                    seed=20261102, scenarios=[asdict(scenario) for scenario in scenarios],
                    piece_length_quantiles_m=np.quantile(lengths, [0, .5, .9, .99, 1]).tolist(),
                    cases_sha256=hashlib.sha256(cases.read_bytes()).hexdigest(),
                    reference_cap_m=.05, convergence_cap_m=.1,
                    note="No production defaults changed. No route search benchmark. All candidate spacings retain min(radius,width)/4. Timed batches rotate variant order after warmup; no profiling feature.")
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    render(args.output, rows, scenarios)
    print(json.dumps(dict(report=str(args.output / "index.html"), geometries=len(geometries), rows=len(rows))))


if __name__ == "__main__":
    main()

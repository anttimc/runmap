"""Render native profiling captures as a standalone HTML report and exportable charts."""

import argparse
import csv
from html import escape
import io
import json
from pathlib import Path
import shutil
from statistics import median
from typing import Literal, TypedDict, cast

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


class Phase(TypedDict):
    milliseconds: float
    calls: int
    allocations: int
    requested_bytes: int


class Sample(TypedDict):
    elapsed_ms: list[float]
    fingerprints: list[str]
    phases: dict[str, list[Phase]]
    result: dict[str, object]


class Capture(TypedDict):
    created_utc: str
    dataset_id: str
    platform: str
    python: str
    revision: str
    nodes: int
    segments: int
    chunks: int
    payload_bytes: int
    edge_layout_bytes: list[int] | None
    workloads: dict[str, dict[str, Sample]]


LABELS = {
    "distance_1km": "Explore 1 km",
    "distance_3km": "Explore 3 km",
    "distance_only_roundtrip": "Distance-only loop",
    "shape_roundtrip": "Shape loop",
    "shape_hills_high_roundtrip": "Shape + hills loop",
}
GROUPS = {
    "Search state": ("initialize",),
    "Geometry buffers": ("decode_geometry", "clip_geometry"),
    "Field integration": ("integration",),
    "Cost / cache": ("cost",),
    "Traversal / queue": ("traversal",),
    "Other": ("views", "overlay", "result", "serialize", "hill_decode", "index"),
}
COLORS = ["#8b5cf6", "#f59e0b", "#06b6d4", "#ec4899", "#3b82f6", "#94a3b8"]


def _value(sample: Sample, phases: tuple[str, ...], field: Literal["milliseconds", "calls", "allocations", "requested_bytes"]) -> float:
    """Aggregate each repetition before taking its median."""
    columns = [sample["phases"][phase] for phase in phases]
    return float(median(sum(float(row[field]) for row in repetition)
                        for repetition in zip(*columns)))


def _verify(baseline: Capture, profile: Capture) -> None:
    for key in ("dataset_id", "revision", "nodes", "segments", "chunks", "payload_bytes"):
        if baseline[key] != profile[key]:
            raise ValueError(f"Capture identities disagree: {key}")
    for name, modes in profile["workloads"].items():
        expected = baseline["workloads"][name]["baseline"]["fingerprints"]
        if len(expected) != 1 or any(run["fingerprints"] != expected for run in modes.values()):
            raise ValueError(f"Non-deterministic or changed results: {name}")


def _chart(baseline: Capture, profile: Capture, output: Path) -> str:
    names = list(LABELS)
    positions = np.arange(len(names))
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False})
    figure, axes = plt.subplots(2, 2, figsize=(16, 11), layout="constrained")
    figure.suptitle("Where native routing spends time and allocates memory", fontsize=21, weight="bold")
    baseline_ms = [median(baseline["workloads"][name]["baseline"]["elapsed_ms"]) for name in names]
    p95 = [float(np.percentile(baseline["workloads"][name]["baseline"]["elapsed_ms"], 95)) for name in names]
    ax = axes[0, 0]
    ax.barh(positions, baseline_ms, color="#2563eb")
    ax.errorbar(baseline_ms, positions, xerr=[np.zeros(len(names)), np.array(p95) - baseline_ms],
                fmt="none", color="#0f172a", capsize=3)
    for i, value in enumerate(baseline_ms):
        ax.text(p95[i] + max(p95) * .025, i, f"{value:.2f} ms", va="center")
    ax.set_xlim(0, max(p95) * 1.3)
    ax.set_yticks(positions, [LABELS[name] for name in names])
    ax.invert_yaxis()
    ax.set(title="A · Normal release build: median, whisker to p95", xlabel="Milliseconds per workload")

    for ax, mode, field, title, scale in (
        (axes[0, 1], "detail", "milliseconds", "B · Detailed timing: exclusive share (instrumented)", 1.0),
        (axes[1, 0], "allocations", "requested_bytes", "C · Rust allocation traffic: bytes requested", 2**20),
        (axes[1, 1], "allocations", "allocations", "D · Rust allocation / reallocation requests", 1000.0),
    ):
        left = np.zeros(len(names))
        for (label, phases), color in zip(GROUPS.items(), COLORS):
            values = np.array([_value(profile["workloads"][name][mode], phases, field) for name in names])
            if field == "milliseconds":
                totals = np.array([_value(profile["workloads"][name][mode], tuple(profile["workloads"][name][mode]["phases"]), field)
                                   for name in names])
                values = values / totals * 100
            else:
                values /= scale
            ax.barh(positions, values, left=left, label=label, color=color)
            left += values
        ax.set_yticks(positions, [LABELS[name] for name in names])
        ax.invert_yaxis()
        ax.set_title(title, loc="left", pad=14)
        ax.set_xlabel("Percent of scoped time; includes timer overhead" if field == "milliseconds"
                      else "MiB requested (not live memory)" if field == "requested_bytes" else "Thousands of requests")
        ax.grid(axis="x", alpha=.15)
        ax.set_axisbelow(True)
    handles, labels = axes[0, 1].get_legend_handles_labels()
    figure.legend(handles, labels, loc="outside lower center", ncol=6, frameon=False)
    figure.savefig(output / "overview.png", dpi=160)
    figure.savefig(output / "overview.svg")
    buffer = io.StringIO()
    figure.savefig(buffer, format="svg")
    plt.close(figure)
    return "<svg" + buffer.getvalue().split("<svg", 1)[1]


def _tables(baseline: Capture, profile: Capture) -> tuple[str, str, list[dict[str, str | float]]]:
    timing_rows: list[str] = []
    phase_rows: list[str] = []
    csv_rows: list[dict[str, str | float]] = []
    for name, label in LABELS.items():
        normal = baseline["workloads"][name]["baseline"]
        modes = profile["workloads"][name]
        durations = [median(normal["elapsed_ms"])] + [median(modes[mode]["elapsed_ms"])
                                                      for mode in ("coarse", "detail", "allocations")]
        timing_rows.append(f"<tr><td>{label}</td>" + "".join(f"<td>{value:.3f}</td>" for value in durations)
                           + f"<td>{normal['result']['expanded_nodes']:,}</td><td>{normal['result']['output_bytes']:,}</td></tr>")
        for phase in modes["detail"]["phases"]:
            timing = _value(modes["detail"], (phase,), "milliseconds")
            count = _value(modes["allocations"], (phase,), "calls")
            allocations = _value(modes["allocations"], (phase,), "allocations")
            size = _value(modes["allocations"], (phase,), "requested_bytes")
            if not count:
                continue
            phase_rows.append(f"<tr><td>{label}</td><td>{phase}</td><td>{timing:.4f}</td>"
                              f"<td>{count:,.0f}</td><td>{allocations:,.0f}</td><td>{size / 2**20:.4f}</td></tr>")
            csv_rows.append(dict(workload=name, phase=phase, exclusive_ms=timing, calls=count,
                                 allocations=allocations, requested_bytes=size))
    return "".join(timing_rows), "".join(phase_rows), csv_rows


def _document(baseline: Capture, profile: Capture, svg: str, timing_rows: str, phase_rows: str) -> str:
    date = profile["created_utc"][:10]
    layout = profile["edge_layout_bytes"]
    if layout is None:
        raise ValueError("The profiling capture must include native layout sizes")
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Native routing profile · {date}</title>
<style>body{{font:16px/1.55 system-ui,sans-serif;color:#172033;background:#f5f7fb;margin:0}}
main{{max-width:1450px;margin:auto;padding:40px}}h1{{font-size:36px;line-height:1.15}}h2{{margin-top:36px}}
.card{{background:white;padding:24px;border-radius:14px;margin:20px 0;overflow:auto}}svg{{width:100%;height:auto}}
table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{padding:9px 12px;text-align:right;border-bottom:1px solid #e2e8f0}}
td:first-child,th:first-child{{text-align:left}}code{{font-size:.9em}}.muted{{color:#536176}}a{{color:#1d4ed8}}
</style><main><p class="muted">Runmap · measured {date} · native Rust routing</p>
<h1>Large state buffers; many small geometry allocations</h1>
<p>Real Helsinki data, {profile['nodes']:,} active nodes, {profile['segments']:,} selected segments,
{profile['chunks']} chunks and {profile['payload_bytes'] / 2**20:.1f} MiB of retained graph payloads.
All result fingerprints match between normal and profiling builds.</p>
<div class="card">{svg}</div>
<div class="card"><h2>Reading the measurements</h2>
<p><b>Investigate geometry/cost work for CPU time, and compact predecessor storage for memory traffic.</b>
Each predecessor entry occupies {layout[1]} bytes. Shape loops perform four searches;
distance-only loops perform two, so their totals are not a controlled per-search comparison.</p>
<p>Timing and allocation captures are separate. Panel A uses the normal release build.
Panel B shows exclusive times from detailed instrumentation, which perturbs short operations;
its percentages are diagnostic, not exact production CPU shares. Panels C and D count successful Rust
allocation/reallocation requests, including the full new size for reallocation. They do not measure live
memory or bytes actually copied. Python and SQLite allocations are excluded.</p>
<p>Graph payloads are borrowed in Rust. Geometry vectors and serialization staging buffers are allocated.
Python-owned output bytes require an additional copy that the Rust allocator counter does not capture;
the output-size column below gives that payload volume. NumPy wraps those bytes without another bulk copy.</p></div>
<div class="card"><h2>Latency and instrumentation overhead</h2><p>Milliseconds, medians; normal build has
{len(next(iter(baseline['workloads'].values()))['baseline']['elapsed_ms'])} samples and profiling passes have
{len(next(iter(profile['workloads'].values()))['detail']['elapsed_ms'])} samples each. Builds run sequentially;
small differences also reflect machine noise and cache state.</p>
<table><thead><tr><th>Workload</th><th>Normal</th><th>Coarse timers</th><th>Detailed timers</th>
<th>Allocation pass</th><th>Expanded nodes</th><th>Output bytes</th></tr></thead><tbody>{timing_rows}</tbody></table></div>
<div class="card"><h2>Exclusive phase measurements</h2>
<p>Times come from the detailed pass; allocation counts and bytes come from the timer-free allocation pass.</p>
<table><thead><tr><th>Workload</th><th>Phase</th><th>ms</th><th>Calls</th><th>Allocation requests</th><th>Requested MiB</th>
</tr></thead><tbody>{phase_rows}</tbody></table></div>
<div class="card"><h2>Scope and reproduction</h2>
<p>Fixed working area around Leppävaara, home (24.795, 60.216), turnaround (24.815, 60.225).
Default loop settings; separate hills-off and hills-high workloads. Each repetition recomputes searches and costs
over a shared immutable graph. No automatic area expansion, HTTP, route rendering, or exact-response cache.
OS cache was uncontrolled. This is a local diagnostic, not a general latency guarantee.</p>
<p>{escape(profile['platform'])}; Python {escape(profile['python'])}. Source revision
<code>{escape(profile['revision'])}</code> plus profiling changes in the working tree.
Dataset <code>{escape(profile['dataset_id'])}</code>.</p>
<p><a href="baseline.json">Normal capture</a> · <a href="instrumented.json">Profiling capture</a> ·
<a href="phases.csv">Phase CSV</a> · <a href="overview.png">PNG</a> · <a href="overview.svg">SVG</a></p></div></main></html>"""


def render(baseline_path: Path, profile_path: Path, output: Path) -> None:
    baseline = cast(Capture, json.loads(baseline_path.read_text()))
    profile = cast(Capture, json.loads(profile_path.read_text()))
    _verify(baseline, profile)
    output.mkdir(parents=True, exist_ok=True)
    for source, name in ((baseline_path, "baseline.json"), (profile_path, "instrumented.json")):
        if source.resolve() != (output / name).resolve():
            shutil.copyfile(source, output / name)
    svg = _chart(baseline, profile, output)
    timing_rows, phase_rows, csv_rows = _tables(baseline, profile)
    with (output / "phases.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    (output / "index.html").write_text(_document(baseline, profile, svg, timing_rows, phase_rows))
    print(f"Wrote {output / 'index.html'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.baseline, args.profile, args.output)


if __name__ == "__main__":
    main()

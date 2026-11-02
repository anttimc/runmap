"""Export width-specific held-start residual ranges for the runtime cubic."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from runmap.experiments.fit_loop_width import Polynomial, cell_weights, fit_polynomial, load_fit_rows
from runmap.experiments.plot_loop_width_sweep import WidthRow

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class ResidualKnot:
    width: float
    lower: float
    upper: float
    positions: int
    routes: int
    empirical_coverage: float


def weighted_quantile(values: FloatArray, weights: FloatArray, probability: float) -> float:
    if (not len(values) or values.shape != weights.shape or not 0 <= probability <= 1
            or not np.all(np.isfinite(values)) or not np.all(np.isfinite(weights)) or np.any(weights <= 0)):
        raise ValueError("Quantiles require finite values, positive matching weights, and a probability")
    order = np.argsort(values, kind="stable")
    ordered = weights[order]
    centres = (np.cumsum(ordered) - ordered / 2) / ordered.sum()
    return float(np.interp(probability, centres, values[order]))


def residual_knots(rows: list[WidthRow], predicted: FloatArray) -> tuple[ResidualKnot, ...]:
    """Include both individual routes, with equal influence per start/radius cell."""
    if len(rows) != len(predicted):
        raise ValueError("Predictions must match observations")
    row_weights = cell_weights(rows)
    knots = []
    for width in sorted({row.width for row in rows}):
        values: list[float] = []
        weights: list[float] = []
        positions = 0
        for index, row in enumerate(rows):
            if row.width != width:
                continue
            positions += 1
            lengths = ([row.mean_length_m] if width == 0 else
                       [row.mean_length_m - row.side_gap_m / 2, row.mean_length_m + row.side_gap_m / 2])
            for length in lengths:
                values.append((length - predicted[index]) / row.separation_m)
                weights.append(float(row_weights[index]) / len(lengths))
        value_array, weight_array = np.asarray(values), np.asarray(weights)
        lower = min(0.0, weighted_quantile(value_array, weight_array, .1))
        upper = max(0.0, weighted_quantile(value_array, weight_array, .9))
        coverage = float(np.average((value_array >= lower) & (value_array <= upper), weights=weight_array))
        knots.append(ResidualKnot(width, lower, upper, positions, len(values), coverage))
    return tuple(knots)


def export_calibration(source: Path, report_path: Path, python_output: Path | None) -> None:
    if report_path.exists():
        raise FileExistsError("Use a fresh report path to preserve calibration evidence")
    rows, identity = load_fit_rows(source)
    fit = fit_polynomial(rows, Polynomial("width", 3))
    knots = residual_knots(rows, fit.held_start)
    raw_hash = identity["manifest"]["samples_sha256"]
    report = {"model": "cubic-extra-v1", "calibration": "held-start-residual-v1",
        "coefficients": fit.coefficients.tolist(), "width_scale": .6, "nominal_mass": .8,
        "quantiles": [.1, .9], "knots": [asdict(knot) for knot in knots],
        "sample_sha256": raw_hash, "dataset_id": identity["manifest"]["dataset_id"],
        "radii_m": sorted({row.radius_m for row in rows}), "starts": sorted({row.start for row in rows}),
        "method": "Weighted midpoint-CDF quantiles of (individual route length - held-start mean prediction)/snapped separation. Equal start/radius cell weight; both orientations; bounds include zero residual. Linear interpolation between width knots.",
        "limits": "Empirical spread from five starts, conditional on all-width success; not independently calibrated coverage. Hills off, all surfaces, stairs avoided. Extrapolation outside sampled radii is unvalidated.",
        "fit_source_sha256": hashlib.sha256(Path(__file__).with_name("fit_loop_width.py").read_bytes()).hexdigest()}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    if python_output is not None:
        lines = [
            '"""Generated cubic coefficients and empirical held-start residual data; see calibrate_turnaround."""',
            '', 'MODEL_VERSION = "cubic-extra-v1"', 'CALIBRATION_VERSION = "held-start-residual-v1"',
            f'COEFFICIENTS: tuple[float, float, float] = {tuple(float(c) for c in fit.coefficients)!r}',
            'WIDTH_SCALE = 0.6', 'NOMINAL_MASS = 0.8', f'SAMPLE_SHA256 = {raw_hash!r}',
            'RESIDUAL_KNOTS: tuple[tuple[float, float, float], ...] = (',
            *[f'    {(knot.width, knot.lower, knot.upper)!r},' for knot in knots], ')', '',
        ]
        python_output.write_text('\n'.join(lines))
    print(json.dumps({"coefficients": report["coefficients"], "knots": report["knots"]}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--python-output", type=Path)
    args = parser.parse_args()
    export_calibration(args.source, args.report, args.python_output)


if __name__ == "__main__":
    main()

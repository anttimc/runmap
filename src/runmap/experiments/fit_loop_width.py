"""Fit anchored width polynomials with whole-start and whole-radius holdouts."""

import argparse
from collections import Counter
from dataclasses import dataclass
import csv
import hashlib
import json
from pathlib import Path
from typing import Literal

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from numpy.typing import NDArray

from runmap.experiments.loop_width_sweep import read_samples
from runmap.experiments.plot_loop_width_sweep import WidthRow, matched_rows, save_plot


FloatArray = NDArray[np.float64]
Family = Literal["width", "detour", "detour_radius"]


@dataclass(frozen=True)
class Polynomial:
    family: Family
    degree: int


@dataclass(frozen=True)
class PolynomialFit:
    model: Polynomial
    coefficients: FloatArray
    fitted: FloatArray
    held_start: FloatArray
    held_radius: FloatArray
    weights: FloatArray
    rank: int
    condition: float


def design_matrix(rows: list[WidthRow], model: Polynomial) -> tuple[FloatArray, tuple[str, ...]]:
    """Predict dimensionless extra distance, retaining the exact zero-width baseline."""
    if not rows or model.degree not in (2, 3, 4):
        raise ValueError("A nonempty dataset and polynomial degree 2, 3, or 4 are required")
    if model.family not in ("width", "detour", "detour_radius"):
        raise ValueError("Unknown polynomial family")
    if any(row.separation_m <= 0 for row in rows):
        raise ValueError("Turnarounds require nonzero separation")
    q = np.array([row.width / .6 for row in rows])
    delta = np.array([row.baseline_m / row.separation_m - 2 for row in rows])
    distance = np.array([row.separation_m / 10000 for row in rows])
    axes = [("1", np.ones(len(rows)))]
    if model.family in ("detour", "detour_radius"):
        axes.append(("detour", delta))
    if model.family == "detour_radius":
        axes.append(("radius", distance))
    columns = [axis * q**power for _, axis in axes for power in range(1, model.degree + 1)]
    terms = tuple(f"{name}*q^{power}" for name, _ in axes for power in range(1, model.degree + 1))
    return np.column_stack(columns), terms


def cell_weights(rows: list[WidthRow]) -> FloatArray:
    """Give each represented start/radius cell equal influence, including sparse coastal starts."""
    counts = Counter((row.start, row.radius_m) for row in rows)
    weights = np.array([1 / counts[row.start, row.radius_m] for row in rows])
    return weights / weights.sum()


def solve(rows: list[WidthRow], model: Polynomial) -> tuple[FloatArray, int, float]:
    x, _ = design_matrix(rows, model)
    target = np.array([(row.mean_length_m - row.baseline_m) / row.separation_m for row in rows])
    weights = np.sqrt(cell_weights(rows))
    coefficients, _, rank, singular = np.linalg.lstsq(x * weights[:, None], target * weights, rcond=None)
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else float("inf")
    return coefficients, int(rank), condition


def predict(rows: list[WidthRow], model: Polynomial, coefficients: FloatArray) -> FloatArray:
    x, _ = design_matrix(rows, model)
    return np.array([row.baseline_m for row in rows]) + np.array([row.separation_m for row in rows]) * (x @ coefficients)


def grouped_predictions(rows: list[WidthRow], model: Polynomial, group: Literal["start", "radius_m"]) -> FloatArray:
    predictions = np.full(len(rows), np.nan)
    labels = [row.start if group == "start" else row.radius_m for row in rows]
    for held in sorted(set(labels)):
        train = [row for row, label in zip(rows, labels) if label != held]
        indices = [index for index, label in enumerate(labels) if label == held]
        if not train:
            raise ValueError("Holdout requires at least two represented groups")
        coefficients, _, _ = solve(train, model)
        predictions[indices] = predict([rows[index] for index in indices], model, coefficients)
    return predictions


def fit_polynomial(rows: list[WidthRow], model: Polynomial) -> PolynomialFit:
    coefficients, rank, condition = solve(rows, model)
    return PolynomialFit(model, coefficients, predict(rows, model, coefficients),
                         grouped_predictions(rows, model, "start"), grouped_predictions(rows, model, "radius_m"),
                         cell_weights(rows), rank, condition)


def error_metrics(rows: list[WidthRow], predicted: FloatArray) -> dict[str, float]:
    actual = np.array([row.mean_length_m for row in rows])
    weights = cell_weights(rows)
    error = predicted - actual
    relative = error / actual
    return {"mae_m": float(weights @ np.abs(error)), "rmse_m": float(np.sqrt(weights @ error**2)),
            "mape_pct": float(100 * (weights @ np.abs(relative))),
            "relative_rmse_pct": float(100 * np.sqrt(weights @ relative**2)),
            "bias_m": float(weights @ error),
            "within_5_pct": float(100 * (weights @ (np.abs(relative) <= .05))),
            "within_10_pct": float(100 * (weights @ (np.abs(relative) <= .10))),
            "unweighted_p90_abs_m": float(np.percentile(np.abs(error), 90))}


def fit_record(rows: list[WidthRow], fit: PolynomialFit) -> dict[str, object]:
    _, terms = design_matrix(rows, fit.model)
    nonzero = [index for index, row in enumerate(rows) if row.width > 0]
    active = [rows[index] for index in nonzero]
    by_start = {}
    by_radius = {}
    by_width = {}
    for name in sorted({row.start for row in rows}):
        indices = [i for i, row in enumerate(rows) if row.start == name and row.width > 0]
        by_start[name] = error_metrics([rows[i] for i in indices], fit.held_start[indices])
    for radius in sorted({row.radius_m for row in rows}):
        indices = [i for i, row in enumerate(rows) if row.radius_m == radius and row.width > 0]
        by_radius[str(radius)] = error_metrics([rows[i] for i in indices], fit.held_start[indices])
    for width in sorted({row.width for row in active}):
        indices = [i for i, row in enumerate(rows) if row.width == width]
        by_width[str(width)] = error_metrics([rows[i] for i in indices], fit.held_start[indices])
    dense_rows = [WidthRow(row.start, row.radius_m, row.bearing_deg, float(width), row.separation_m,
        row.baseline_m, row.mean_length_m, row.side_gap_m, row.estimate_m, row.error_pct)
        for row in rows if row.width == 0 for width in np.linspace(0, .6, 121)]
    dense = predict(dense_rows, fit.model, fit.coefficients).reshape(-1, 121)
    baselines = np.array([row.baseline_m for row in rows if row.width == 0])[:, None]
    return {"family": fit.model.family, "degree": fit.model.degree, "terms": terms,
        "coefficients": fit.coefficients.tolist(), "rank": fit.rank, "condition": fit.condition,
        "train": error_metrics(active, fit.fitted[nonzero]),
        "held_start": error_metrics(active, fit.held_start[nonzero]),
        "held_radius": error_metrics(active, fit.held_radius[nonzero]),
        "held_start_by_start": by_start, "held_start_by_radius": by_radius, "held_start_by_width": by_width,
        "dense_violations": {"positions_below_baseline": int(np.any(dense < baselines - 1e-6, axis=1).sum()),
                             "positions_with_decreasing_width_length": int(np.any(np.diff(dense, axis=1) < -1, axis=1).sum())}}


def fit_curves(output: Path, rows: list[WidthRow], fit: PolynomialFit, radii: list[float], widths: list[float],
               plot_name: str = "polynomial-width-curves") -> None:
    fig, axes = plt.subplots((len(radii) + 1) // 2, 2, figsize=(13, 4.5 * ((len(radii) + 1) // 2)),
                             squeeze=False, layout="constrained")
    for radius, ax in zip(radii, axes.ravel()):
        selected = [row for row in rows if row.radius_m == radius]
        for name, color in (("mean_length_m", "#193c38"), ("estimate_m", "#a66b35")):
            mean = [np.mean([getattr(row, name) for row in selected if row.width == width]) / 1000 for width in widths]
            ax.plot(np.asarray(widths) * 100, mean, "o-" if name == "mean_length_m" else "--",
                    color=color, label="Measured mean" if name == "mean_length_m" else "Ellipse")
        indices = [[i for i, row in enumerate(rows) if row.radius_m == radius and row.width == width] for width in widths]
        actual = [np.percentile([rows[i].mean_length_m for i in index], [10, 90]) / 1000 for index in indices]
        ax.fill_between(np.asarray(widths) * 100, np.asarray(actual)[:, 0], np.asarray(actual)[:, 1],
                        color="#64748b", alpha=.18, label="Measured 10th–90th percentile")
        ax.plot(np.asarray(widths) * 100, [np.mean(fit.fitted[index]) / 1000 for index in indices],
                color="#087f8c", linewidth=2.4, label="Polynomial: full-data fitted mean")
        ax.plot(np.asarray(widths) * 100, [np.mean(fit.held_start[index]) / 1000 for index in indices],
                color="#8c4a93", linestyle=":", linewidth=2, label="Polynomial: held-start predictions")
        ax.set(title=f"{radius/1000:g} km turnaround · n={sum(row.width == 0 for row in selected)}",
               xlabel="Loop width (%)", ylabel="Loop length (km)")
        ax.grid(alpha=.2)
    axes.ravel()[len(radii) - 1].legend(fontsize=8)
    for ax in axes.ravel()[len(radii):]:
        ax.axis("off")
    fig.suptitle(f"Anchored {fit.model.family} polynomial, degree {fit.model.degree} — Lauttasaari included\nCurves pool retained positions; fitting balances start/radius cells; spread averages orientations", fontsize=12)
    save_plot(output, plot_name)


def validation_plots(output: Path, rows: list[WidthRow], fits: list[PolynomialFit], best: PolynomialFit,
                     records: list[dict[str, object]]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), layout="constrained")
    labels = [f"{fit.model.family} d{fit.model.degree}" for fit in fits]
    metrics = [record["held_start"]["relative_rmse_pct"] for record in records]
    axes[0].barh(labels, metrics, color=["#087f8c" if fit is best else "#abbdb6" for fit in fits])
    axes[0].set(xlabel="Held-start relative RMSE (%) · nonzero widths", title="Whole-start holdout comparison")
    for start in sorted({row.start for row in rows}):
        indices = [i for i, row in enumerate(rows) if row.start == start and row.width > 0]
        actual = np.array([rows[i].mean_length_m for i in indices])
        axes[1].scatter(actual / 1000, best.held_start[indices] / 1000, label=start, alpha=.55, s=12)
    maximum = max(max(row.mean_length_m for row in rows), float(np.max(best.held_start))) / 1000
    axes[1].plot([0, maximum], [0, maximum], "--", color="#193c38")
    axes[1].set(xlabel="Actual loop length (km)", ylabel="Held-start prediction (km)", title="Selected model · predictions at unseen starts")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=.2)
    save_plot(output, "polynomial-validation")


def load_fit_rows(source: Path) -> tuple[list[WidthRow], dict[str, object]]:
    manifest = json.loads((source / "manifest.json").read_text())
    raw = source / "samples.jsonl.gz"
    if not manifest["complete"] or hashlib.sha256(raw.read_bytes()).hexdigest() != manifest["samples_sha256"]:
        raise ValueError("Fit requires complete, unchanged sampling")
    samples = list(read_samples(raw, retain_geometry=False))
    if len(samples) != manifest["evaluations"]:
        raise ValueError("Sample count changed")
    rows, excluded = matched_rows(samples, tuple(manifest["config"]["widths"]))
    if len({row.start for row in rows}) < 2 or len({row.radius_m for row in rows}) < 2:
        raise ValueError("Validation requires multiple starts and radii")
    return rows, {"manifest": manifest, "excluded_positions": excluded}


def run_fits(source: Path, output: Path) -> None:
    rows, identity = load_fit_rows(source)
    output.mkdir(parents=True, exist_ok=False)
    fits = [fit_polynomial(rows, Polynomial(family, degree))
            for family in ("width", "detour", "detour_radius") for degree in (2, 3, 4)]
    records = [fit_record(rows, fit) for fit in fits]
    best_index = min(range(len(fits)), key=lambda index: records[index]["held_start"]["relative_rmse_pct"])
    best = fits[best_index]
    active = [row for row in rows if row.width > 0]
    report = {**identity, "rows": len(rows), "positions": sum(row.width == 0 for row in rows),
        "selection": "Lowest start/radius-balanced relative RMSE in leave-one-start-out predictions, nonzero widths only. Exploratory model selection, not an untouched final test.",
        "loss": "Weighted squared error of (L-baseline)/r; equal total weight per represented start/radius cell. Both orientations averaged before fitting. No outlier removal, clipping or robust loss.",
        "equation": "L=b+r*[P(q)+(b/r-2)*Q(q)+(r/10000)*S(q)], q=width/0.6; polynomials have no constant terms; omit Q/S for simpler families",
        "selected_index": best_index, "models": records,
        "ellipse": error_metrics(active, np.array([row.estimate_m for row in active])),
        "analysis_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (output / "fit.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    with (output / "predictions.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["start", "radius_m", "bearing_deg", "width", "actual_m", "baseline_m", "ellipse_m", "fitted_m", "held_start_m", "held_radius_m"])
        writer.writerows([row.start, row.radius_m, row.bearing_deg, row.width, row.mean_length_m, row.baseline_m,
                          row.estimate_m, fitted, held_start, held_radius]
                         for row, fitted, held_start, held_radius in zip(rows, best.fitted, best.held_start, best.held_radius))
    radii = sorted({row.radius_m for row in rows})
    widths = sorted({row.width for row in rows})
    fit_curves(output, rows, best, radii, widths)
    simple = next(fit for fit in fits if fit.model.family == "width" and fit.model.degree == 3)
    fit_curves(output, rows, simple, radii, widths, "simple-cubic-width-curves")
    validation_plots(output, rows, fits, best, records)
    print(json.dumps({"selected": records[best_index], "ellipse": report["ellipse"]}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run_fits(args.source, args.output)


if __name__ == "__main__":
    main()

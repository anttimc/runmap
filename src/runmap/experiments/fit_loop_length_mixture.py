"""Compare MAP polynomial mixtures using a baseline experiment's saved observations."""

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np

from runmap.experiments.fit_loop_length import (
    LengthObservation, design_matrix, error_metrics, fit_model, spatial_folds,
)
from runmap.experiments.loop_length_features import NetworkFeature
from runmap.experiments.loop_length_grid import GridPoint, source_identity
from runmap.experiments.loop_length_mixture import (
    FloatArray, MixtureConfig, MixtureFit, fit_mixture, predict_mixture,
)


def load_observations(analysis: Path) -> list[LengthObservation]:
    rows: list[LengthObservation] = []
    seen: set[tuple[int, str]] = set()
    with (analysis / "predictions.csv").open(newline="") as stream:
        for record in csv.DictReader(stream):
            if record["model"] != "network_affine":
                continue
            key = int(record["sample_id"]), record["side"]
            if key in seen or key[1] not in ("left", "right"):
                raise ValueError("Duplicate observation or unknown side in baseline predictions")
            seen.add(key)
            i, j = int(record["forward_index"]), int(record["lateral_index"])
            forward, lateral = float(record["forward_m"]), float(record["lateral_m"])
            # Geographic coordinates are unused by fitting; preserve the sampled frame.
            point = GridPoint(key[0], i, j, int(record["width_index"]), forward, lateral,
                              float(record["width"]), 0.0, 0.0)
            feature = NetworkFeature(i, j, float(record["radius_m"]), float(record["outward_m"]),
                                     float(record["returning_m"]), forward, lateral)
            rows.append(LengthObservation(point, key[1], float(record["actual_m"]), feature))
    if not rows:
        raise ValueError("Baseline predictions contain no network_affine observations")
    return rows


def mixture_features(rows: list[LengthObservation]) -> tuple[FloatArray, FloatArray, FloatArray]:
    experts, _, _ = design_matrix(rows, "network_affine")
    gates = np.array([[1, row.point.forward_m, row.point.lateral_m, row.point.width / .18,
                       (row.network.outward_m + row.network.returning_m - 2 * row.network.radius_m) / 1000,
                       float(row.side == "right")] for row in rows])
    return experts, gates, np.array([row.length_m for row in rows])


def fit_record(fit: MixtureFit) -> dict[str, object]:
    return {"config": asdict(fit.config),
            "expert_scaling": {"mean": fit.expert_scaling.mean.tolist(), "scale": fit.expert_scaling.scale.tolist()},
            "gate_scaling": {"mean": fit.gate_scaling.mean.tolist(), "scale": fit.gate_scaling.scale.tolist()},
            "rbf_centers": None if fit.rbf_centers is None else fit.rbf_centers.tolist(),
            "coefficients": fit.coefficients.tolist(), "gate_coefficients": fit.gate_coefficients.tolist(),
            "variances_m2": fit.variances.tolist(), "objective_history": fit.objective_history,
            "converged": fit.converged, "effective_counts": fit.effective_counts.tolist(), "restart": fit.restart}


def validate_spatial(rows: list[LengthObservation], config: MixtureConfig, block_size: int,
                     buffer: int) -> tuple[dict[str, object], list[dict[str, object]], MixtureFit]:
    experts, gates, actual = mixture_features(rows)
    soft, hard, log_density = (np.full(len(rows), np.nan) for _ in range(3))
    records: list[dict[str, object]] = []
    folds: list[dict[str, object]] = []
    for fold, (train, test) in enumerate(spatial_folds(rows, block_size, buffer)):
        print(f"  Spatial fold {fold + 1}", flush=True)
        fit = fit_mixture(experts[train], gates[train], actual[train], config)
        prediction = predict_mixture(fit, experts[test], gates[test], actual[test])
        soft[test], hard[test] = prediction.mean, prediction.hard
        assert prediction.log_density is not None
        log_density[test] = prediction.log_density
        folds.append({"fold": fold, "train_count": int(train.sum()), "test_count": int(test.sum()),
                      "fit": fit_record(fit)})
        for index, mean, selected, variance, density, weights in zip(
                np.flatnonzero(test), prediction.mean, prediction.hard, prediction.variance,
                prediction.log_density, prediction.weights):
            row = rows[index]
            records.append({"components": config.components, "sample_id": row.point.sample_id, "side": row.side,
                            "fold": fold, "actual_m": row.length_m, "mean_m": float(mean),
                            "hard_m": float(selected), "variance_m2": float(variance),
                            "log_density": float(density), "weights": json.dumps(weights.tolist())})
    if not all(np.isfinite(values).all() for values in (soft, hard, log_density)):
        raise ValueError("Incomplete spatial predictions")
    baseline = fit_model(rows, "network_affine", block_size, buffer)
    full = fit_mixture(experts, gates, actual, config)
    summary: dict[str, object] = {
        "full_data_fit": fit_record(full), "folds": folds,
        "spatial_holdout": {"soft": error_metrics(actual, soft), "hard": error_metrics(actual, hard),
                            "mean_negative_log_density": float(-log_density.mean()),
                            "baseline_network_affine": error_metrics(actual, baseline.held_out)},
        "by_width": {str(width): error_metrics(actual[mask], soft[mask])
                     for width in sorted({row.point.width for row in rows})
                     if (mask := np.array([row.point.width == width for row in rows])).any()},
        "by_side": {side: error_metrics(actual[mask], soft[mask]) for side in ("left", "right")
                    if (mask := np.array([row.side == side for row in rows])).any()}}
    return summary, records, full


def analyze_mixtures(analysis: Path, output: Path, configs: list[MixtureConfig],
                     block_size: int = 5, buffer: int = 1,
                     plot_widths: tuple[float, ...] = (.12, .36)) -> None:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a fresh mixture output directory")
    source_report = json.loads((analysis / "report.json").read_text())
    rows = load_observations(analysis)
    available = {row.point.width for row in rows}
    if not plot_widths or any(width not in available for width in plot_widths):
        raise ValueError(f"Plot widths must be sampled widths: {sorted(available)}")
    output.mkdir(parents=True, exist_ok=True)
    models: dict[str, object] = {}
    records: list[dict[str, object]] = []
    for config in configs:
        print(f"Fitting K={config.components}", flush=True)
        summary, predictions, full = validate_spatial(rows, config, block_size, buffer)
        models[str(config.components)] = summary
        records.extend(predictions)
        from runmap.experiments.plot_loop_length_mixture import plot_categories
        plot_categories(output, rows, full, plot_widths)
        print(f"Finished K={config.components}; spatial metrics and convergence are saved in report.json", flush=True)
    report = {"schema_version": 2, "created_utc": datetime.now(timezone.utc).isoformat(),
              "source": source_identity(), "input_analysis": str(analysis.resolve()),
              "input_predictions_sha256": hashlib.sha256((analysis / "predictions.csv").read_bytes()).hexdigest(),
              "baseline_report": source_report, "observations": len(rows),
              "validation": {"block_size": block_size, "buffer": buffer, "selection": "exploratory; no nested tuning"},
              "definitions": {"response": "metres", "expert_basis": design_matrix(rows, "network_affine")[2],
                              "gate_basis": ["1", "forward_m", "lateral_m", "q", "detour_km", "right"],
                              "rbf_basis": "linear terms plus exp(-||standardized(f,l,q)-center||^2/(2*h^2)); side last",
                              "category_plots": {"widths": plot_widths, "fit": "full data; gate probabilities without observed lengths",
                                                 "labels": "arbitrary component indices; consistent only within a fitted model"},
                              "coefficient_basis": "training-standardized columns; explicit intercept",
                              "prior_mean": "zero", "reference_gate": "last component has zero logits",
                              "variance": "mixture residual spread; excludes parameter uncertainty"},
              "models": models}
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    with (output / "predictions.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path, help="Existing baseline fit directory")
    parser.add_argument("output", type=Path)
    parser.add_argument("--components", type=int, nargs="+", default=[1, 2, 3, 4])
    parser.add_argument("--restarts", type=int, default=4)
    parser.add_argument("--max-iterations", type=int, default=300)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--block-size", type=int, default=5)
    parser.add_argument("--buffer", type=int, default=1)
    parser.add_argument("--coefficient-sd-m", type=float, default=3000)
    parser.add_argument("--intercept-sd-m", type=float, default=10000)
    parser.add_argument("--gate-sd", type=float, default=3)
    parser.add_argument("--variance-shape", type=float, default=2)
    parser.add_argument("--variance-scale-m2", type=float, default=270000)
    parser.add_argument("--gate", choices=("linear", "rbf"), default="linear")
    parser.add_argument("--rbf-count", type=int, default=16)
    parser.add_argument("--rbf-length-scale", type=float, default=.8)
    parser.add_argument("--plot-widths", type=float, nargs="+", default=[.12, .36])
    args = parser.parse_args()
    configs = [MixtureConfig(components=k, restarts=args.restarts, max_iterations=args.max_iterations,
                            seed=args.seed, coefficient_sd_m=args.coefficient_sd_m,
                            intercept_sd_m=args.intercept_sd_m, gate_sd=args.gate_sd,
                            variance_shape=args.variance_shape, variance_scale_m2=args.variance_scale_m2,
                            gate=args.gate, rbf_count=args.rbf_count, rbf_length_scale=args.rbf_length_scale)
               for k in args.components]
    analyze_mixtures(args.analysis, args.output, configs, args.block_size, args.buffer, tuple(args.plot_widths))


if __name__ == "__main__":
    main()

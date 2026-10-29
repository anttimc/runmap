"""Fit small loop-length baselines and analyze buffered spatial holdout residuals."""

import argparse
from collections import Counter
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
from time import perf_counter
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from runmap.experiments.loop_length_features import NetworkFeature, network_features
from runmap.experiments.loop_length_grid import GridPoint, GridSample, iter_samples, source_identity
from runmap.routing.profiles import exploration_profile


ModelName = Literal["radial", "radial_angular", "radial_affine", "network_scaled", "network_anchored", "network_affine"]
MODELS: tuple[ModelName, ...] = ("radial", "radial_angular", "radial_affine", "network_scaled", "network_anchored", "network_affine")
FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class LengthObservation:
    point: GridPoint
    side: str
    length_m: float
    network: NetworkFeature


@dataclass(frozen=True)
class ModelFit:
    name: ModelName
    terms: tuple[str, ...]
    coefficients: FloatArray
    fitted: FloatArray
    held_out: FloatArray
    folds: NDArray[np.int64]
    rank: int
    condition_number: float


def load_samples(path: Path, expected: int) -> list[GridSample]:
    samples: list[GridSample] = []
    for sample in iter_samples(path):
        # Retain scientific labels and snapping; release large rendering payloads.
        if sample.response is not None:
            for candidate in sample.response.candidates:
                for leg in candidate.legs:
                    leg.coordinates.clear()
                    leg.traversals.clear()
        samples.append(sample)
    if [sample.point.sample_id for sample in samples] != list(range(expected)):
        raise ValueError("Grid records are missing, duplicated, or out of order")
    return samples


def length_observations(samples: list[GridSample], features: list[NetworkFeature]) -> list[LengthObservation]:
    lookup = {(feature.forward_index, feature.lateral_index): feature for feature in features}
    rows: list[LengthObservation] = []
    for sample in samples:
        response = sample.response
        if response is None or response.status != "complete":
            continue
        feature = lookup[sample.point.forward_index, sample.point.lateral_index]
        baseline = feature.outward_m + feature.returning_m
        for candidate in response.candidates:
            if baseline > candidate.distance_m + .02:
                raise ValueError("Unpenalized network distance exceeds a measured feasible loop")
            rows.append(LengthObservation(sample.point, "left" if candidate.outbound_left else "right",
                                          candidate.distance_m, feature))
    return rows


def design_matrix(rows: list[LengthObservation], name: ModelName) -> tuple[FloatArray, FloatArray, tuple[str, ...]]:
    r = np.array([row.network.radius_m for row in rows])
    a = np.array([row.point.width / .18 for row in rows])
    b = np.array([row.network.outward_m + row.network.returning_m for row in rows])
    powers = np.column_stack((np.ones(len(rows)), a, a * a))
    offset = np.zeros(len(rows))
    if name == "radial":
        return r[:, None] * powers, offset, ("r", "r*a", "r*a^2")
    if name == "network_scaled":
        return b[:, None] * powers, offset, ("b", "b*a", "b*a^2")
    if name == "network_anchored":
        return r[:, None] * powers[:, 1:], b, ("r*a", "r*a^2")
    if name == "radial_angular":
        u = np.array([row.network.forward_m for row in rows])
        v = np.array([row.network.lateral_m for row in rows])
        return np.column_stack([axis[:, None] * powers for axis in (r, u, v)]), offset, tuple(
            f"{axis}{suffix}" for axis in ("r", "u", "v") for suffix in ("", "*a", "*a^2"))
    if name in ("radial_affine", "network_affine"):
        axes = (np.ones(len(rows)), r / 1000) if name == "radial_affine" else (np.ones(len(rows)), r / 1000, (b - 2 * r) / 1000)
        names = ("1", "r_km") if name == "radial_affine" else ("1", "r_km", "detour_km")
        return np.column_stack([axis[:, None] * powers for axis in axes]), offset, tuple(
            f"{axis}{suffix}" for axis in names for suffix in ("", "*a", "*a^2"))
    raise ValueError(f"Unknown baseline: {name}")


def spatial_folds(rows: list[LengthObservation], block_size: int, buffer: int
                  ) -> list[tuple[NDArray[np.bool_], NDArray[np.bool_]]]:
    if block_size < 1 or buffer < 0:
        raise ValueError("Block size must be positive and buffer nonnegative")
    i = np.array([row.point.forward_index for row in rows])
    j = np.array([row.point.lateral_index for row in rows])
    blocks = sorted(set(zip((i // block_size).tolist(), (j // block_size).tolist())))
    if len(blocks) < 2:
        raise ValueError("Spatial validation requires at least two populated blocks")
    result = []
    for bi, bj in blocks:
        test = (i // block_size == bi) & (j // block_size == bj)
        near = ((i >= bi * block_size - buffer) & (i < (bi + 1) * block_size + buffer)
                & (j >= bj * block_size - buffer) & (j < (bj + 1) * block_size + buffer))
        result.append((~near, test))
    return result


def fit_model(rows: list[LengthObservation], name: ModelName, block_size: int, buffer: int) -> ModelFit:
    x, offset, terms = design_matrix(rows, name)
    y = np.array([row.length_m for row in rows])
    coefficients, _, rank, singular = np.linalg.lstsq(x, y - offset, rcond=None)
    if rank != x.shape[1]:
        raise ValueError(f"{name} design matrix is rank deficient")
    predicted = np.full(len(rows), np.nan)
    folds = np.full(len(rows), -1, dtype=np.int64)
    for fold, (train, test) in enumerate(spatial_folds(rows, block_size, buffer)):
        weights, _, fold_rank, _ = np.linalg.lstsq(x[train], (y - offset)[train], rcond=None)
        if fold_rank != x.shape[1]:
            raise ValueError(f"{name} training fold {fold} is rank deficient")
        predicted[test] = offset[test] + x[test] @ weights
        folds[test] = fold
    if not np.all(np.isfinite(predicted)):
        raise ValueError("Not every observation received a held-out prediction")
    return ModelFit(name, terms, coefficients, offset + x @ coefficients, predicted, folds,
                    int(rank), float(singular[0] / singular[-1]))


def error_metrics(actual: FloatArray, predicted: FloatArray) -> dict[str, float | int]:
    residual = actual - predicted
    absolute = np.abs(residual)
    variation = float(np.sum((actual - actual.mean()) ** 2))
    return {"count": len(actual), "mae_m": float(absolute.mean()),
        "rmse_m": float(np.sqrt(np.mean(residual ** 2))), "bias_actual_minus_predicted_m": float(residual.mean()),
        "p95_absolute_m": float(np.percentile(absolute, 95)), "max_absolute_m": float(absolute.max()),
        "r2": 1 - float(np.sum(residual ** 2)) / variation if variation > 0 else 0.0,
        "within_100m_fraction": float(np.mean(absolute <= 100)),
        "within_250m_fraction": float(np.mean(absolute <= 250)),
        "within_5percent_fraction": float(np.mean(absolute <= actual * .05))}


def residual_structure(rows: list[LengthObservation], residuals: FloatArray) -> dict[str, float | int | None]:
    lookup = {(row.point.forward_index, row.point.lateral_index, row.point.width_index, row.side): float(value)
              for row, value in zip(rows, residuals)}
    pairs = [(value, lookup[(i + di, j + dj, k, side)]) for (i, j, k, side), value in lookup.items()
             for di, dj in ((1, 0), (0, 1)) if (i + di, j + dj, k, side) in lookup]
    first, second = np.array(pairs).T if pairs else (np.array([]), np.array([]))
    correlation = float(np.corrcoef(first, second)[0, 1]) if len(pairs) > 2 and first.std() > 0 and second.std() > 0 else None
    return {"adjacent_pairs": len(pairs), "adjacent_residual_correlation": correlation,
            "median_adjacent_residual_difference_m": float(np.median(np.abs(first - second))) if pairs else None}


def model_summary(rows: list[LengthObservation], fit: ModelFit) -> dict[str, object]:
    actual = np.array([row.length_m for row in rows])
    positive = np.array([row.point.width > 0 for row in rows])
    return {"terms": fit.terms, "coefficients": fit.coefficients.tolist(), "rank": fit.rank,
        "condition_number": fit.condition_number, "in_sample": error_metrics(actual, fit.fitted),
        "spatial_holdout": error_metrics(actual, fit.held_out),
        "spatial_holdout_nonzero_width": error_metrics(actual[positive], fit.held_out[positive]),
        "by_width": {str(width): error_metrics(actual[mask], fit.held_out[mask]) for width in sorted({row.point.width for row in rows})
                     if (mask := np.array([row.point.width == width for row in rows])).any()},
        "in_sample_residual_structure": residual_structure(rows, actual - fit.fitted),
        "held_out_residual_structure": residual_structure(rows, actual - fit.held_out),
        "largest_held_out_errors": [{"sample_id": rows[k].point.sample_id, "side": rows[k].side,
            "forward_m": rows[k].point.forward_m, "lateral_m": rows[k].point.lateral_m,
            "width": rows[k].point.width, "actual_m": float(actual[k]), "prediction_m": float(fit.held_out[k]),
            "residual_m": float(actual[k] - fit.held_out[k])}
            for k in np.argsort(np.abs(actual - fit.held_out))[-10:][::-1]]}


def save_predictions(output: Path, rows: list[LengthObservation], fits: list[ModelFit]) -> None:
    with (output / "predictions.csv").open("w", newline="") as stream:
        columns = ("model", "sample_id", "side", "forward_index", "lateral_index", "width_index", "forward_m",
                   "lateral_m", "width", "radius_m", "outward_m", "returning_m", "actual_m", "fitted_m",
                   "held_out_m", "residual_m", "fold")
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for fit in fits:
            for k, row in enumerate(rows):
                writer.writerow(dict(model=fit.name, sample_id=row.point.sample_id, side=row.side,
                    forward_index=row.point.forward_index, lateral_index=row.point.lateral_index,
                    width_index=row.point.width_index, forward_m=row.point.forward_m, lateral_m=row.point.lateral_m,
                    width=row.point.width, radius_m=row.network.radius_m, outward_m=row.network.outward_m,
                    returning_m=row.network.returning_m, actual_m=row.length_m, fitted_m=fit.fitted[k],
                    held_out_m=fit.held_out[k], residual_m=row.length_m - fit.held_out[k], fold=int(fit.folds[k])))


def side_spread(rows: list[LengthObservation]) -> dict[str, float | int | None]:
    pairs: dict[int, list[float]] = {}
    for row in rows:
        pairs.setdefault(row.point.sample_id, []).append(row.length_m)
    spread = np.array([abs(values[0] - values[1]) for values in pairs.values() if len(values) == 2])
    return {"paired_evaluations": len(spread),
        "p50_m": float(np.percentile(spread, 50)) if len(spread) else None,
        "p95_m": float(np.percentile(spread, 95)) if len(spread) else None,
        "max_m": float(spread.max()) if len(spread) else None,
        "minimum_mae_for_shared_side_prediction_m": float(spread.sum() / len(rows))}


def analyze_grid(grid: Path, database: Path, output: Path, block_size: int = 5, buffer: int = 1) -> None:
    manifest = json.loads((grid / "manifest.json").read_text())
    if not manifest["complete"]:
        raise ValueError("Grid sampling is incomplete")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a fresh analysis output directory")
    output.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    samples = load_samples(grid / "samples.jsonl.gz", manifest["sample_count"])
    config = manifest["config"]
    features, feature_metadata = network_features(database, samples, manifest["dataset_id"],
        exploration_profile(config["surface"], config["avoid_stairs"]), config["bearing_deg"])
    rows = length_observations(samples, features)
    fits = [fit_model(rows, name, block_size, buffer) for name in MODELS]
    zero_difference = [abs(row.length_m - row.network.outward_m - row.network.returning_m)
                       for row in rows if row.point.width == 0]
    report: dict[str, object] = {"created_utc": datetime.now(timezone.utc).isoformat(), "schema_version": 1,
        "machine": platform.platform(), "python": platform.python_version(),
        "source": source_identity(), "grid_manifest": manifest,
        "raw_samples_sha256": hashlib.sha256((grid / "samples.jsonl.gz").read_bytes()).hexdigest(),
        "observations": len(rows), "positions": len(features), "feature_computation": feature_metadata,
        "side_assignment_spread": side_spread(rows),
        "zero_width_baseline_max_difference_m": max(zero_difference) if zero_difference else None,
        "definitions": {"a": "width / 0.18", "r": "snapped projected separation in metres",
            "u_v": "snapped forward and lateral displacement in metres", "b": "outward + returning unpenalized directed network distance in metres",
            "r_km_detour_km": "r_km = r/1000; detour_km = (b-2*r)/1000; corresponding coefficients are in metres",
            "network_anchored": "b + fitted r*a and r*a^2 terms; all other models are sums of fitted terms",
            "residual": "actual minus predicted physical loop length, in metres"},
        "validation": {"method": "Leave one spatial block out; all widths and both sides grouped by position",
            "block_size_cells": block_size, "buffer_cells": buffer, "folds": int(fits[0].folds.max() + 1),
            "noisy_observations": "Excluded missing, deadline, area-limited and planner-error evaluations; no fabricated labels"},
        "evaluation_statuses": dict(Counter("planner_error" if s.response is None else s.response.status for s in samples)),
        "models": {fit.name: model_summary(rows, fit) for fit in fits}, "analysis_s": perf_counter() - started}
    save_predictions(output, rows, fits)
    (output / "network-features.json").write_text(json.dumps([asdict(feature) for feature in features], indent=2) + "\n")
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    from runmap.experiments.plot_loop_length_fit import plot_analysis
    plot_analysis(output, rows, fits)
    for fit in fits:
        print(fit.name, error_metrics(np.array([row.length_m for row in rows]), fit.held_out), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("grid", type=Path)
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--block-size", type=int, default=5)
    parser.add_argument("--buffer", type=int, default=1)
    args = parser.parse_args()
    analyze_grid(args.grid, args.database, args.output, args.block_size, args.buffer)


if __name__ == "__main__":
    main()

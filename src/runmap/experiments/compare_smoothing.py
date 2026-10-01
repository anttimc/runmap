"""Compare terrain smoothers and create a magnitude/direction figure."""

import argparse
import json
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import Normalize
from rasterio.windows import from_bounds

from runmap.terrain.derivatives import terrain_gradient
from runmap.terrain.smoothing import gaussian_smooth, nonlinear_diffusion, thin_plate_smooth


def summary(name, surface, source, resolution, seconds):
    east, north, slope = terrain_gradient(surface, resolution, resolution)
    return {"name": name, "seconds": seconds,
            "elevation_change_rms_m": float(np.sqrt(np.mean((surface - source) ** 2))),
            "elevation_change_max_abs_m": float(np.max(np.abs(surface - source))),
            "slope_percentiles_percent": {str(q): float(100 * np.percentile(slope, q))
                                           for q in (50, 90, 95, 99, 99.9)}}, (east, north, slope)


def plot_synthetic_derivative_cross_sections(truth, candidates, resolution, output_dir,
                                             profiles=None, case_name="Synthetic"):
    """Plot selected synthetic derivatives against the known analytic truth."""
    size = truth.shape[0]
    eastings = (np.arange(truth.shape[1]) - size / 2) * resolution
    selected_names = {
        "Raw", "Gaussian σ=4 m", "Gaussian σ=8 m", "Thin plate half-amplitude=40 m"
    }
    selected = [("Analytic truth", truth)] + [
        (("Noisy/raw", surface) if name == "Raw" else (name, surface))
        for name, surface in candidates if name in selected_names
    ]
    styles = [
        {"color": "#111111", "linewidth": 2.0, "linestyle": "--", "zorder": 5},
        {"color": "#777777", "linewidth": 1.0, "alpha": 0.65},
        {"color": "#e69f00", "linewidth": 1.5},
        {"color": "#7570b3", "linewidth": 1.7},
        {"color": "#00796b", "linewidth": 1.7},
    ]
    if profiles is None:
        profiles = [("ridge", 70.0), ("hill-centre", 0.0), ("south", -100.0)]
    records = []
    for label, requested_northing in profiles:
        row = int(round(size / 2 - requested_northing / resolution))
        row = int(np.clip(row, 0, truth.shape[0] - 1))
        northing = float((size / 2 - row) * resolution)
        fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, constrained_layout=True)
        profile_axis, derivative_axis = axes
        for (name, surface), style in zip(selected, styles):
            east, _, _ = terrain_gradient(surface, resolution, resolution)
            profile_axis.plot(eastings, surface[row, :], label=name, **style)
            derivative_axis.plot(eastings, 100 * east[row, :], label=name, **style)
        derivative_axis.axhline(0, color="#999999", linewidth=0.8, zorder=0)
        fig.suptitle(f"{case_name} west–east cross-section at y={northing:.0f} m ({label})")
        profile_axis.set_ylabel("Elevation (m)")
        derivative_axis.set_xlabel("Synthetic easting (m)")
        derivative_axis.set_ylabel("Signed eastward grade (%)")
        for axis in axes:
            axis.grid(True, color="#d8d8d8", linewidth=0.6, alpha=0.8)
        profile_axis.legend(ncol=3, fontsize=8, framealpha=0.9)
        path = output_dir / f"synthetic-derivative-{label}.png"
        fig.savefig(path, dpi=180)
        plt.close(fig)
        records.append({"label": label, "orientation": "west-east", "row": row,
                        "synthetic_northing_m": northing, "path": str(path)})
    return records


def sharp_feature_comparison(resolution, output_dir):
    """Compare selected smoothers on a continuous ridge with sharp corners."""
    size = 300
    rows, cols = np.mgrid[0:size, 0:size]
    x = (cols - size / 2) * resolution
    y = (size / 2 - rows) * resolution
    ridge = 6.0 * np.maximum(1.0 - np.abs(x) / 12.0, 0.0) * np.exp(-y*y / (2 * 100**2))
    truth = 20.0 + 0.01 * x + 0.005 * y + ridge
    rng = np.random.default_rng(20261001)
    noisy = truth + rng.normal(0, 0.20, truth.shape)
    candidates = [
        ("Raw", noisy),
        ("Gaussian σ=4 m", gaussian_smooth(noisy, 4, resolution)),
        ("Gaussian σ=8 m", gaussian_smooth(noisy, 8, resolution)),
        ("Thin plate half-amplitude=40 m", thin_plate_smooth(noisy, 40, resolution)),
    ]
    plots = plot_synthetic_derivative_cross_sections(
        truth, candidates, resolution, output_dir,
        profiles=[("sharp-feature", 0.0)], case_name="Sharp-feature synthetic",
    )
    interior = np.s_[20:-20, 20:-20]
    true_e, true_n, true_s = terrain_gradient(truth, resolution, resolution)
    methods = []
    for name, surface in candidates:
        east, north, slope = terrain_gradient(surface, resolution, resolution)
        methods.append({
            "name": name,
            "elevation_rmse_m": float(np.sqrt(np.mean((surface[interior] - truth[interior]) ** 2))),
            "gradient_vector_rmse_percent": float(100 * np.sqrt(np.mean(
                (east[interior] - true_e[interior]) ** 2
                + (north[interior] - true_n[interior]) ** 2))),
            "peak_slope_ratio": float(np.max(slope[interior]) / np.max(true_s[interior])),
        })
    return {
        "surface": "tilted plane + 6 m high, 24 m wide triangular ridge",
        "noise": "independent Gaussian elevation noise, σ=0.20 m, seed=20261001",
        "feature": "continuous elevation with abrupt slope changes at ridge foot and crest",
        "methods": methods,
        "derivative_cross_sections": plots,
    }


def synthetic_comparison(resolution=2.0, output_dir=None):
    """Measure denoising and feature retention against an analytic surface."""
    size = 300
    rows, cols = np.mgrid[0:size, 0:size]
    x = (cols - size / 2) * resolution
    y = (size / 2 - rows) * resolution
    # A tilted base, broad rounded hill, and narrower ridge-like hill.
    truth = (20 + 0.015 * x + 0.01 * y
             + 18 * np.exp(-(x*x + y*y) / (2 * 90**2))
             + 7 * np.exp(-((x + 145)**2 / (2 * 28**2) + (y - 70)**2 / (2 * 100**2))))
    rng = np.random.default_rng(20260905)
    noisy = truth + rng.normal(0, 0.20, truth.shape)
    true_e, true_n, true_s = terrain_gradient(truth, resolution, resolution)
    candidates = [
        ("Raw", noisy),
        ("Gaussian σ=2 m", gaussian_smooth(noisy, 2, resolution)),
        ("Gaussian σ=4 m", gaussian_smooth(noisy, 4, resolution)),
        ("Gaussian σ=8 m", gaussian_smooth(noisy, 8, resolution)),
        ("Thin plate half-amplitude=20 m", thin_plate_smooth(noisy, 20, resolution)),
        ("Thin plate half-amplitude=40 m", thin_plate_smooth(noisy, 40, resolution)),
        ("Nonlinear κ=8%, t=4 px²", nonlinear_diffusion(
            noisy, resolution, iterations=20, time_step=0.2, kappa=0.08, guide_sigma_m=2)),
    ]
    cross_sections = (plot_synthetic_derivative_cross_sections(
        truth, candidates, resolution, output_dir
    ) if output_dir is not None else [])
    interior = np.s_[20:-20, 20:-20]
    directional = true_s[interior] >= 0.01
    result = []
    for name, surface in candidates:
        east, north, slope = terrain_gradient(surface, resolution, resolution)
        dot = east[interior] * true_e[interior] + north[interior] * true_n[interior]
        denom = np.maximum(slope[interior] * true_s[interior], 1e-12)
        angle = np.degrees(np.arccos(np.clip(dot / denom, -1, 1)))
        result.append({
            "name": name,
            "elevation_rmse_m": float(np.sqrt(np.mean((surface[interior] - truth[interior]) ** 2))),
            "gradient_vector_rmse_percent": float(100 * np.sqrt(np.mean(
                (east[interior] - true_e[interior]) ** 2 +
                (north[interior] - true_n[interior]) ** 2))),
            "median_direction_error_degrees_where_truth_at_least_1_percent":
                float(np.median(angle[directional])),
            "p95_direction_error_degrees_where_truth_at_least_1_percent":
                float(np.percentile(angle[directional], 95)),
            "peak_slope_ratio": float(np.max(slope[interior]) / np.max(true_s[interior])),
        })
    return {"surface": "tilted plane + broad hill + narrow ridge",
            "noise": "independent Gaussian elevation noise, σ=0.20 m, seed=20260905",
            "evaluation": "20-cell boundary excluded; direction evaluated where true slope >=1%",
            "methods": result, "derivative_cross_sections": cross_sections,
            "sharp_feature": (sharp_feature_comparison(resolution, output_dir)
                              if output_dir is not None else None)}


def plot_derivative_cross_sections(results, transform, output_dir):
    """Plot signed eastward derivatives along representative west-east profiles."""
    requested = [
        ("north", 6679600.0),
        ("central", 6679000.0),
        ("south", 6678400.0),
    ]
    width = results[0][1].shape[1]
    height = results[0][1].shape[0]
    eastings = transform.c + (np.arange(width) + 0.5) * transform.a
    selected_names = {
        "Raw", "Gaussian σ=4 m", "Gaussian σ=8 m", "Thin plate half-amplitude=40 m"
    }
    selected = [result for result in results if result[0] in selected_names]
    styles = [
        {"color": "#202020", "linewidth": 1.2, "alpha": 0.75},
        {"color": "#e69f00", "linewidth": 1.5},
        {"color": "#7570b3", "linewidth": 1.7},
        {"color": "#00796b", "linewidth": 1.7},
    ]
    records = []
    for label, requested_northing in requested:
        row = int(round((requested_northing - transform.f) / transform.e - 0.5))
        row = int(np.clip(row, 0, height - 1))
        northing = float(transform.f + (row + 0.5) * transform.e)
        fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, constrained_layout=True)
        profile_axis, derivative_axis = axes
        for (name, surface, (east, _, _)), style in zip(selected, styles):
            profile_axis.plot(eastings, surface[row, :], label=name, **style)
            derivative_axis.plot(eastings, 100 * east[row, :], label=name, **style)
        derivative_axis.axhline(0, color="#777777", linewidth=0.8, zorder=0)
        fig.suptitle(f"West–east cross-section at N {northing:.0f} m (EPSG:3067)")
        profile_axis.set_ylabel("Elevation (m)")
        derivative_axis.set_xlabel("Easting (m, EPSG:3067)")
        derivative_axis.set_ylabel("Signed eastward grade (%)")
        for axis in axes:
            axis.grid(True, color="#d8d8d8", linewidth=0.6, alpha=0.8)
        profile_axis.legend(ncol=2, fontsize=8, framealpha=0.9)
        derivative_axis.ticklabel_format(style="plain", axis="x")
        path = output_dir / f"derivative-cross-section-{label}.png"
        fig.savefig(path, dpi=180)
        plt.close(fig)
        records.append({"label": label, "orientation": "west-east", "quantity": "100 * dz/dx",
                        "sign_convention": "positive rises eastward", "row": row,
                        "northing_epsg3067": northing, "path": str(path)})
    return records


def run(source_path, output_dir, crop_bounds):
    output_dir.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source_path) as src:
        window = from_bounds(*crop_bounds, transform=src.transform).round_offsets().round_lengths()
        z = src.read(1, window=window).astype(np.float64)
        transform = src.window_transform(window)
        resolution = float(src.res[0])
        profile = src.profile.copy()
    methods = [("Raw", lambda: z),
               ("Gaussian σ=2 m", lambda: gaussian_smooth(z, 2, resolution)),
               ("Gaussian σ=4 m", lambda: gaussian_smooth(z, 4, resolution)),
               ("Gaussian σ=8 m", lambda: gaussian_smooth(z, 8, resolution)),
               ("Thin plate half-amplitude=20 m", lambda: thin_plate_smooth(z, 20, resolution)),
               ("Thin plate half-amplitude=40 m", lambda: thin_plate_smooth(z, 40, resolution)),
               ("Nonlinear κ=8%, t=4 px²", lambda: nonlinear_diffusion(
                   z, resolution, iterations=20, time_step=0.2, kappa=0.08, guide_sigma_m=2))]
    reports, results = [], []
    for name, operation in methods:
        start = time.perf_counter()
        surface = operation()
        elapsed = time.perf_counter() - start
        report, gradients = summary(name, surface, z, resolution, elapsed)
        reports.append(report)
        results.append((name, surface, gradients))

    derivative_cross_sections = plot_derivative_cross_sections(results, transform, output_dir)

    raw_999 = reports[0]["slope_percentiles_percent"]["99.9"]
    vmax = min(40.0, max(10.0, raw_999))
    fig, axes = plt.subplots(len(results), 2, figsize=(13, 3.3 * len(results)), constrained_layout=True)
    extent = [crop_bounds[0], crop_bounds[2], crop_bounds[1], crop_bounds[3]]
    for row, (name, surface, (east, north, slope)) in enumerate(results):
        magnitude = axes[row, 0].imshow(100 * slope, extent=extent, origin="upper",
                                        cmap="magma", norm=Normalize(0, vmax))
        axes[row, 0].set_title(f"{name}: slope magnitude (%)")
        axes[row, 1].imshow(100 * slope, extent=extent, origin="upper",
                            cmap="Greys", norm=Normalize(0, vmax), alpha=0.65)
        step = 40
        rows = np.arange(step // 2, z.shape[0], step)
        cols = np.arange(step // 2, z.shape[1], step)
        xx = transform.c + (cols + 0.5) * transform.a
        yy = transform.f + (rows + 0.5) * transform.e
        ee = east[np.ix_(rows, cols)]
        nn = north[np.ix_(rows, cols)]
        mag = np.hypot(ee, nn)
        keep = mag >= 0.01
        axes[row, 1].quiver(*np.meshgrid(xx, yy), np.where(keep, ee / np.maximum(mag, 1e-12), np.nan),
                            np.where(keep, nn / np.maximum(mag, 1e-12), np.nan),
                            color="#087f8c", angles="xy", scale_units="xy", scale=0.012, width=0.003)
        axes[row, 1].set_title(f"{name}: uphill direction (arrows; ≥1%)")
        for axis in axes[row]:
            axis.set_aspect("equal")
            axis.ticklabel_format(style="plain")
    fig.colorbar(magnitude, ax=axes[:, 0], label="slope (%)", shrink=0.7)
    fig.savefig(output_dir / "smoothing-comparison.png", dpi=160)
    plt.close(fig)

    report = {"source": str(source_path), "crop_bounds_epsg3067": list(crop_bounds),
              "shape": list(z.shape), "resolution_m": resolution,
              "direction": "Arrows point uphill relative to EPSG:3067 grid north",
              "arrow_threshold_percent": 1.0,
              "methods": reports, "derivative_cross_sections": derivative_cross_sections,
              "synthetic_validation": synthetic_comparison(resolution, output_dir),
              "caveat": "Descriptive comparison only; feature preservation needs synthetic and local validation."}
    (output_dir / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/raw/leppavaara/elevation-2m.tif"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/leppavaara-smoothing"))
    parser.add_argument("--bounds", nargs=4, type=float, metavar=("W", "S", "E", "N"),
                        default=(376000, 6678000, 378000, 6680000),
                        help="EPSG:3067 comparison crop; default is a central 2 km square")
    args = parser.parse_args()
    report = run(args.source, args.output, tuple(args.bounds))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

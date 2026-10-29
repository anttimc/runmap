"""Visualize held-out loop-length predictions and structured residuals."""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
import numpy as np

from runmap.experiments.fit_loop_length import LengthObservation, ModelFit
from runmap.experiments.plot_loop_length_grid import save_figure


def plot_predictions(output: Path, rows: list[LengthObservation], fits: list[ModelFit]) -> None:
    actual = np.array([row.length_m for row in rows]) / 1000
    fig, axes = plt.subplots(2, 3, figsize=(15, 10), layout="constrained")
    for ax, fit in zip(axes.ravel(), fits):
        predicted = fit.held_out / 1000
        ax.scatter(actual, predicted, s=4, alpha=.15, rasterized=True)
        ax.plot([0, actual.max()], [0, actual.max()], "k--", linewidth=1)
        mae = np.abs(actual - predicted).mean() * 1000
        ax.set(title=f"{fit.name} · held-out MAE {mae:.0f} m", xlabel="Actual length (km)",
               ylabel="Predicted length (km)", aspect="equal")
        ax.grid(alpha=.2)
    fig.suptitle("Leave-one-spatial-block-out predictions\nAll widths and sides grouped; one-cell training exclusion buffer")
    save_figure(output, "prediction-comparison")


def plot_residual_maps(output: Path, rows: list[LengthObservation], fits: list[ModelFit]) -> None:
    forward = sorted({row.point.forward_m for row in rows})
    lateral = sorted({row.point.lateral_m for row in rows})
    widths = sorted({row.point.width for row in rows})
    chosen = sorted({0, min(range(len(widths)), key=lambda k: abs(widths[k] - .18)), len(widths) - 1})
    # Keep original grid coordinates even where every evaluation at a site failed.
    u = np.linspace(min(forward), max(forward), max(row.point.forward_index for row in rows) + 1)
    v = np.linspace(min(lateral), max(lateral), max(row.point.lateral_index for row in rows) + 1)
    actual = np.array([row.length_m for row in rows])
    limit = max(float(np.max(np.abs(actual - fit.held_out))) for fit in fits)
    norm = Normalize(-limit, limit)
    cmap = plt.get_cmap("RdBu_r").copy()
    cmap.set_bad("#dddddd")
    fig, axes = plt.subplots(len(fits), len(chosen), figsize=(15, 3.5 * len(fits)), squeeze=False, layout="constrained")
    for fit, pair in zip(fits, axes):
        for width_index, ax in zip(chosen, pair):
            total = np.zeros((len(u), len(v)))
            counts = np.zeros_like(total)
            for row, residual in zip(rows, actual - fit.held_out):
                if row.point.width_index == width_index:
                    i, j = row.point.forward_index, row.point.lateral_index
                    total[i, j] += residual
                    counts[i, j] += 1
            surface = np.divide(total, counts, out=np.full_like(total, np.nan), where=counts > 0)
            image = ax.pcolormesh(v, u, np.ma.masked_invalid(surface), norm=norm, cmap=cmap, shading="nearest")
            ax.set(title=f"{fit.name} · width {widths[width_index]:.2f}", xlabel="Requested lateral offset (m)",
                   ylabel="Requested forward distance (m)", aspect="equal")
    fig.colorbar(image, ax=axes.ravel().tolist(), label="Actual − held-out prediction (m); averaged across sides", shrink=.65)
    fig.suptitle("Held-out residual maps · shared, unclipped color scale\nGrey = no complete label; warm colors = underprediction")
    save_figure(output, "residual-maps")


def plot_residual_inputs(output: Path, rows: list[LengthObservation], fits: list[ModelFit]) -> None:
    actual = np.array([row.length_m for row in rows])
    radius = np.array([row.network.radius_m for row in rows])
    width = np.array([row.point.width for row in rows])
    detour = np.array([(row.network.outward_m + row.network.returning_m) / (2 * row.network.radius_m) for row in rows])
    fig, axes = plt.subplots(len(fits), 3, figsize=(14, 3 * len(fits)), sharey=True, layout="constrained")
    for fit, pair in zip(fits, axes):
        residual = actual - fit.held_out
        for x, label, ax in zip((radius / 1000, width, detour),
                               ("Snapped radius (km)", "Width setting", "Network round trip / (2 × radius)"), pair):
            ax.scatter(x, residual, s=4, alpha=.12, rasterized=True)
            ax.axhline(0, color="black", linewidth=.8)
            ax.set(xlabel=label, title=fit.name)
            ax.grid(alpha=.2)
        pair[0].set_ylabel("Actual − held-out prediction (m)")
    fig.suptitle("Residuals against model inputs\nRepeated widths and two side assignments are correlated observations")
    save_figure(output, "residual-inputs")


def plot_error_by_width(output: Path, rows: list[LengthObservation], fits: list[ModelFit]) -> None:
    widths = sorted({row.point.width for row in rows})
    actual = np.array([row.length_m for row in rows])
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), layout="constrained")
    for fit in fits:
        error = actual - fit.held_out
        groups = [error[np.array([row.point.width == width for row in rows])] for width in widths]
        axes[0].plot(widths, [np.abs(group).mean() for group in groups], "o-", label=fit.name)
        axes[1].plot(widths, [group.mean() for group in groups], "o-", label=fit.name)
    axes[0].set_ylabel("Held-out mean absolute error (m)")
    axes[1].set_ylabel("Held-out mean residual (m)")
    for ax in axes:
        ax.set_xlabel("Width setting")
        ax.grid(alpha=.2)
        ax.legend()
    fig.suptitle("Error versus width · actual minus predicted length\nThe anchored network baseline is exact at zero width when shortest-path labels agree")
    save_figure(output, "error-by-width")


def plot_analysis(output: Path, rows: list[LengthObservation], fits: list[ModelFit]) -> None:
    plot_predictions(output, rows, fits)
    plot_residual_maps(output, rows, fits)
    plot_residual_inputs(output, rows, fits)
    plot_error_by_width(output, rows, fits)

"""Visualize input-only gate categories from one full-data mixture fit."""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
import numpy as np

from runmap.experiments.fit_loop_length import LengthObservation
from runmap.experiments.fit_loop_length_mixture import mixture_features
from runmap.experiments.loop_length_mixture import FloatArray, MixtureFit, predict_mixture


def category_grid(rows: list[LengthObservation], values: FloatArray, width: float, side: str
                  ) -> tuple[FloatArray, tuple[float, float, float, float]]:
    forward = sorted({row.point.forward_m for row in rows})
    lateral = sorted({row.point.lateral_m for row in rows})
    grid = np.full((len(forward), len(lateral)), np.nan)
    fi, li = {v: k for k, v in enumerate(forward)}, {v: k for k, v in enumerate(lateral)}
    for row, value in zip(rows, values):
        if row.point.width == width and row.side == side:
            grid[fi[row.point.forward_m], li[row.point.lateral_m]] = value
    df = (forward[-1] - forward[0]) / max(1, len(forward) - 1) if len(forward) > 1 else 1.0
    dl = (lateral[-1] - lateral[0]) / max(1, len(lateral) - 1) if len(lateral) > 1 else 1.0
    return grid, (lateral[0] - dl / 2, lateral[-1] + dl / 2, forward[0] - df / 2, forward[-1] + df / 2)


def draw_panel(axis: Axes, rows: list[LengthObservation], values: FloatArray, width: float,
               side: str, colors: ListedColormap, norm: BoundaryNorm | None) -> None:
    grid, extent = category_grid(rows, values, width, side)
    axis.imshow(np.ma.masked_invalid(grid), origin="lower", extent=extent, interpolation="nearest",
                cmap=colors, norm=norm, vmin=0 if norm is None else None, vmax=1 if norm is None else None)
    axis.set(title=f"Width {width:g} · outbound {side}", xlabel="Lateral displacement (m)",
             ylabel="Forward displacement (m)")


def plot_categories(output: Path, rows: list[LengthObservation], fit: MixtureFit,
                    widths: tuple[float, ...]) -> None:
    experts, gates, _ = mixture_features(rows)
    prediction = predict_mixture(fit, experts, gates)
    categories = prediction.weights.argmax(axis=1).astype(float)
    colors = ListedColormap(plt.get_cmap("tab10")(np.arange(fit.config.components) % 10))
    colors.set_bad("#dddddd")
    norm = BoundaryNorm(np.arange(fit.config.components + 1) - .5, fit.config.components)
    confidence_colors = ListedColormap(plt.get_cmap("viridis")(np.linspace(0, 1, 256)))
    confidence_colors.set_bad("#dddddd")
    for name, values, cmap, boundaries in (
            ("categories", categories, colors, norm),
            ("gate-confidence", prediction.weights.max(axis=1), confidence_colors, None)):
        figure, axes = plt.subplots(2, len(widths), figsize=(5 * len(widths), 9), squeeze=False,
                                    constrained_layout=True)
        for i, side in enumerate(("left", "right")):
            for j, width in enumerate(widths):
                draw_panel(axes[i, j], rows, values, width, side, cmap, boundaries)
        title = "Most probable category" if name == "categories" else "Maximum gate probability (0–1)"
        figure.suptitle(f"{title} · {fit.config.gate.upper()} gate · K={fit.config.components}\n"
                       "One full-data fit; grey = no successful observation; labels are arbitrary")
        if name == "categories":
            handles = [Patch(color=colors(k), label=f"Category {k + 1}") for k in range(fit.config.components)]
            figure.legend(handles=handles, loc="outside lower center", ncol=fit.config.components)
        else:
            figure.colorbar(axes[0, 0].images[0], ax=axes.ravel().tolist(), label="max πₖ(x)", shrink=.7)
        stem = output / f"{fit.config.gate}-k{fit.config.components}-{name}"
        figure.savefig(stem.with_suffix(".png"), dpi=170)
        figure.savefig(stem.with_suffix(".svg"))
        plt.close(figure)

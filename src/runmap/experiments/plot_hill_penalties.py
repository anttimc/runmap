"""Plot the current running energy model and weighted routing contributions."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from runmap.routing.cost_types import PreferenceWeightTables
from runmap.routing.hills import hill_penalty_response, running_energy_cost, running_energy_response


def plot_penalties(output: Path) -> None:
    energy_slopes = np.linspace(-60, 60, 1201)
    slopes = np.linspace(-20, 20, 801)
    responses = np.array([hill_penalty_response(float(slope)) for slope in slopes])
    energies = np.array([running_energy_cost(float(slope)) for slope in energy_slopes])
    weights = PreferenceWeightTables().hills
    fig, (energy_axis, cost_axis) = plt.subplots(1, 2, figsize=(13, 6.3))
    fig.subplots_adjust(left=0.075, right=0.985, bottom=0.25, top=0.82, wspace=0.25)
    fig.patch.set_facecolor("#faf9f5")
    for axis in (energy_axis, cost_axis):
        axis.set_facecolor("#faf9f5")
        axis.axvline(0, color="#9aaba4", linewidth=0.8)
        axis.grid(axis="y", alpha=0.2)
        axis.spines[["top", "right"]].set_visible(False)
        axis.set_xlabel("Slope (%)  ·  downhill ← 0 → uphill", labelpad=10)
    energy_axis.axvspan(-60, -45, color="#e5e4df")
    energy_axis.axvspan(45, 60, color="#e5e4df")
    energy_axis.set_xlim(-60, 60)
    energy_axis.set_xticks([-60, -45, -30, -15, 0, 15, 30, 45, 60])
    energy_axis.plot(energy_slopes, energies, color="#345b82", linewidth=2.5)
    energy_axis.set_title("Running energy: Minetti Cr", loc="left", pad=14)
    energy_axis.set_ylabel("Energy per travelled metre (J/kg/m)")
    energy_axis.scatter([0], [3.6], color="#345b82", zorder=3)
    energy_axis.annotate("Flat: 3.6", xy=(0, 3.6), xytext=(4, 8), fontsize=10)
    for label, weight, color, style in (
        ("High", weights.high, "#bc5335", "-"),
        ("Low", weights.low, "#087f8c", "-"),
        ("Off", weights.off, "#798480", "--"),
    ):
        assert weight is not None
        cost_axis.plot(slopes, 100 * weight * responses, label=label,
                       color=color, linewidth=2.5, linestyle=style)
    cost_axis.set_xlim(-20, 20)
    cost_axis.set_xticks([-20, -10, 0, 5, 10, 15, 20])
    original = np.array([max(0, running_energy_response(float(slope)) - 1) for slope in slopes])
    assert weights.high is not None
    cost_axis.plot(slopes, 100 * weights.high * original, label="High · unadjusted Cr",
                   color="#bc5335", linewidth=1.5, linestyle=":")
    cost_axis.axvline(5, color="#9aaba4", linewidth=0.8, linestyle=":")
    cost_axis.set_title("Hill preference (detail)", loc="left", pad=14)
    cost_axis.set_ylabel("Preference cost per 100 projected m")
    cost_axis.legend(loc="upper left", frameon=False)
    energy_axis.set_ylim(bottom=0)
    cost_axis.set_ylim(bottom=0)
    fig.suptitle("Favoring gentler climbs", fontsize=18, fontweight="bold", y=0.97)
    fig.supxlabel("Uphill excess above flat is scaled by (grade / 5%)². Flat and moderate downhill add no penalty. "
                  "Route score also includes distance and shape costs.\n"
                  "Base: Minetti et al. (2002), Figure 1 · doi:10.1152/japplphysiol.01177.2001. "
                  "Shaded: tangent continuation beyond ±45%.",
                  fontsize=9, color="#596568", y=0.04)
    fig.savefig(output.with_suffix(".png"), dpi=180)
    svg_path = output.with_suffix(".svg")
    fig.savefig(svg_path)
    svg_path.write_text("\n".join(line.rstrip() for line in svg_path.read_text().splitlines()) + "\n")
    plt.close(fig)


def main() -> None:
    plot_penalties(Path("docs/32-hill-penalties"))


if __name__ == "__main__":
    main()

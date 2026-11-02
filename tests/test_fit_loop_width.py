"""Validate anchored empirical polynomials and leakage-free grouped predictions."""

from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from runmap.experiments.fit_loop_width import Polynomial, cell_weights, fit_polynomial, grouped_predictions, predict, fit_curves
from runmap.experiments.plot_loop_width_sweep import WidthRow


def synthetic_rows() -> list[WidthRow]:
    rows = []
    for start_index, start in enumerate(("Leppävaara", "Pasila", "Keskuspuisto", "Lauttasaari", "Viikki")):
        for radius in (1500, 3000, 5000, 10000):
            for bearing in range(start_index + 2):
                delta = .05 * start_index + .03 * bearing + .00001 * radius
                baseline = radius * (2 + delta)
                for width in (0, .12, .3, .42, .6):
                    q, s = width / .6, radius / 10000
                    length = baseline + radius * (.5*q + 1.2*q*q + delta*(.3*q - .1*q*q) + s*(.2*q + .6*q*q))
                    rows.append(WidthRow(start, radius, bearing*30, width, radius, baseline, length, 100, baseline, 0))
    return rows


class FitLoopWidthTests(unittest.TestCase):
    def test_requested_plot_names_keep_model_figures_distinct(self) -> None:
        rows = synthetic_rows()
        fit = fit_polynomial(rows, Polynomial("width", 3))
        with TemporaryDirectory() as directory:
            output = Path(directory)
            fit_curves(output, rows, fit, [1500, 3000, 5000, 10000], [0, .12, .3, .42, .6], "simple-cubic")
            self.assertTrue((output / "simple-cubic.png").exists())
            self.assertTrue((output / "simple-cubic.svg").exists())
            self.assertFalse((output / "estimate_m.png").exists())

    def test_known_polynomial_recovers_with_unseen_starts_and_radii(self) -> None:
        rows = synthetic_rows()
        fit = fit_polynomial(rows, Polynomial("detour_radius", 2))
        np.testing.assert_allclose(fit.coefficients, [.5, 1.2, .3, -.1, .2, .6], atol=1e-10)
        actual = [row.mean_length_m for row in rows]
        np.testing.assert_allclose(fit.held_start, actual, atol=1e-7)
        np.testing.assert_allclose(fit.held_radius, actual, atol=1e-7)

    def test_each_start_radius_cell_has_equal_weight_including_lauttasaari(self) -> None:
        rows = synthetic_rows()
        weights = cell_weights(rows)
        totals: dict[tuple[str, float], float] = defaultdict(float)
        for row, weight in zip(rows, weights):
            totals[row.start, row.radius_m] += weight
        np.testing.assert_allclose(list(totals.values()), 1 / 20)
        self.assertEqual(sum(row.start == "Lauttasaari" for row in rows), 100)

    def test_zero_width_is_exact_without_clipping(self) -> None:
        rows = synthetic_rows()
        coefficients = np.array([-50., 100.])
        zeros = [row for row in rows if row.width == 0]
        np.testing.assert_allclose(predict(zeros, Polynomial("width", 2), coefficients), [row.baseline_m for row in zeros])

    def test_held_start_labels_cannot_affect_its_predictions(self) -> None:
        rows = synthetic_rows()
        model = Polynomial("detour_radius", 2)
        original = grouped_predictions(rows, model, "start")
        perturbed = [replace(row, mean_length_m=row.mean_length_m + 100000) if row.start == "Lauttasaari" else row
                     for row in rows]
        other = grouped_predictions(perturbed, model, "start")
        indices = [i for i, row in enumerate(rows) if row.start == "Lauttasaari"]
        np.testing.assert_allclose(other[indices], original[indices])

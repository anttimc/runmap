"""Check scientific fits, spatial leakage protection, and directed network features."""

from dataclasses import replace
import math
import sqlite3
import unittest

import numpy as np

from runmap.experiments.fit_loop_length import LengthObservation, design_matrix, error_metrics, fit_model, spatial_folds
from runmap.experiments.loop_length_features import NetworkFeature, distance_to_snap
from runmap.experiments.loop_length_grid import GridPoint
from runmap.paths.exploration import Snap
from runmap.routing.numeric_exploration import NumericDistanceData
from runmap.routing.numeric_search import NumericDistances


def synthetic_rows() -> list[LengthObservation]:
    rows = []
    for i in range(8):
        for j in range(8):
            u, v = (i + 1) * 100.0, (j - 3.5) * 100.0
            radius = math.hypot(u, v)
            feature = NetworkFeature(i, j, radius, 1.2 * radius + 30 * j, 1.3 * radius, u, v)
            for k, width in enumerate((0.0, .12, .24, .42)):
                point = GridPoint((i * 8 + j) * 4 + k, i, j, k, u, v, width, 24.8, 60.2)
                a = width / .18
                length = radius * (1.8 + .6 * a + .08 * a * a)
                for side in (("left",) if k == 0 else ("left", "right")):
                    rows.append(LengthObservation(point, side, length, feature))
    return rows


class LoopLengthFitTests(unittest.TestCase):
    def test_recovers_known_polynomial_in_buffered_spatial_holdout(self) -> None:
        rows = synthetic_rows()
        fit = fit_model(rows, "radial", 2, 1)
        np.testing.assert_allclose(fit.coefficients, (1.8, .6, .08), atol=1e-12)
        actual = np.array([row.length_m for row in rows])
        self.assertLess(error_metrics(actual, fit.held_out)["max_absolute_m"], 1e-8)

    def test_every_width_and_side_is_held_out_together_with_buffer(self) -> None:
        rows = synthetic_rows()
        visits = np.zeros(len(rows), dtype=int)
        for train, test in spatial_folds(rows, 2, 1):
            visits += test
            test_positions = {(row.point.forward_index, row.point.lateral_index) for row, selected in zip(rows, test) if selected}
            for row, in_train, in_test in zip(rows, train, test):
                position = row.point.forward_index, row.point.lateral_index
                if position in test_positions:
                    self.assertTrue(in_test)
                    self.assertFalse(in_train)
                if in_train:
                    self.assertTrue(all(max(abs(position[0] - i), abs(position[1] - j)) > 1 for i, j in test_positions))
        np.testing.assert_array_equal(visits, np.ones(len(rows)))
        with self.assertRaises(ValueError):
            spatial_folds(rows, 100, 0)

    def test_affine_model_recovers_nonzero_radial_intercept(self) -> None:
        rows = synthetic_rows()
        rows = [replace(row, length_m=row.length_m + 200 + 75 * row.point.width / .18) for row in rows]
        fit = fit_model(rows, "radial_affine", 2, 1)
        actual = np.array([row.length_m for row in rows])
        self.assertLess(error_metrics(actual, fit.held_out)["max_absolute_m"], 1e-8)

    def test_network_anchor_is_exact_at_zero_width_and_recovers_detour(self) -> None:
        rows = synthetic_rows()
        rows = [replace(row, length_m=row.network.outward_m + row.network.returning_m
                        + row.network.radius_m * (.4 * row.point.width / .18 + .07 * (row.point.width / .18) ** 2)) for row in rows]
        fit = fit_model(rows, "network_anchored", 2, 1)
        np.testing.assert_allclose(fit.coefficients, (.4, .07), atol=1e-12)
        actual = np.array([row.length_m for row in rows])
        self.assertLess(error_metrics(actual, fit.held_out)["max_absolute_m"], 1e-8)
        x, offset, _ = design_matrix(rows, "network_anchored")
        zero = np.array([row.point.width == 0 for row in rows])
        self.assertTrue(np.all(x[zero] == 0))
        np.testing.assert_allclose(fit.held_out[zero], offset[zero])

    def test_returning_distance_reverses_partial_segment_policy(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            connection.executescript("""
                CREATE TABLE nodes (id TEXT);
                INSERT INTO nodes VALUES ('a'), ('b');
                CREATE TABLE segments (id TEXT, source TEXT, target TEXT, payload TEXT);
                INSERT INTO segments VALUES ('s', 'a', 'b', '{"length_m":100}');
                CREATE TABLE segment_policies (segment_id TEXT, policy_id TEXT, forward INTEGER, backward INTEGER, reason TEXT);
                INSERT INTO segment_policies VALUES ('s', 'test', 1, 0, NULL);
            """)
            values = NumericDistances(np.array([1, 2], dtype=np.int64), np.array([1000., 20.]),
                                      np.array([], dtype=np.int64), 2, -1)
            distances = NumericDistanceData(Snap("home", 0, 0, 24.8, 60.2, 0), 2000, values)
            snap = Snap("s", 25, 25, 24.8, 60.2, 0)
            self.assertEqual(distance_to_snap(connection, distances, snap, "test"), 1025)
            self.assertEqual(distance_to_snap(connection, distances, snap, "test", reverse=True), 95)


if __name__ == "__main__":
    unittest.main()

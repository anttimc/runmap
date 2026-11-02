"""Turnaround estimates preserve directed accessibility and width geometry."""

import math
import unittest

import numpy as np

from test_distance_shells import distances, straight_chunk
from runmap.routing.numeric_search import NumericEndpoint
from runmap.routing.turnaround import (cubic_extra_ratio, ellipse_extra_ratio, round_trip_samples,
    target_region_score, width_residual_interval)


class TurnaroundTests(unittest.TestCase):
    def test_cubic_preserves_baseline_and_moves_wide_turnarounds_closer(self) -> None:
        self.assertEqual(cubic_extra_ratio(0), 0)
        values = [cubic_extra_ratio(float(width)) for width in np.linspace(0, .6, 121)]
        self.assertTrue(all(a < b for a, b in zip(values, values[1:])))
        self.assertGreater(cubic_extra_ratio(.5), ellipse_extra_ratio(.5))
        # The 50%-width fitted curve predicts roughly 15.5 km for r=3 km, b=7.3 km.
        self.assertAlmostEqual(7300 + 3000 * cubic_extra_ratio(.5), 15488, delta=20)

    def test_residual_interpolation_and_zero_width(self) -> None:
        self.assertEqual(width_residual_interval(0), (0, 0))
        a, b, middle = width_residual_interval(.42), width_residual_interval(.5), width_residual_interval(.46)
        np.testing.assert_allclose(middle, np.mean([a, b], axis=0))
        for width in (-.01, .61, float("nan")):
            with self.assertRaises(ValueError):
                cubic_extra_ratio(width)

    def test_uncertainty_region_matches_target_interval_overlap(self) -> None:
        separation = np.full(100, 1000.)
        predicted = np.linspace(5000, 16000, 100)
        baseline = predicted - separation * cubic_extra_ratio(.6)
        lower, upper = width_residual_interval(.6)
        possible = ((baseline <= 10500) & (predicted + separation * upper >= 9500)
                    & (np.maximum(baseline, predicted + separation * lower) <= 10500))
        score = target_region_score(baseline, separation, 10000, .6)
        np.testing.assert_array_equal(score <= 2, possible)
        np.testing.assert_array_equal(score <= 1, (predicted >= 9500) & (predicted <= 10500))
        self.assertTrue(np.isnan(score[baseline > 10500]).all())

    def test_circle_and_zero_width_limits(self) -> None:
        self.assertEqual(ellipse_extra_ratio(0), 0)
        self.assertAlmostEqual(ellipse_extra_ratio(0.5), math.pi - 2)

    def test_partial_bidirectional_start(self) -> None:
        start = NumericEndpoint(segment_id=7, graph_offset_m=400)
        result = round_trip_samples(straight_chunk(), distances([10, 20], [400, 600]),
            distances([10, 20], [400, 600]), start, 0, 1000)
        expected = 2 * np.abs(result.xy[:, 0] - 375400)
        np.testing.assert_allclose(result.distances, expected)

    def test_one_way_partial_start_requires_legal_return(self) -> None:
        start = NumericEndpoint(segment_id=7, graph_offset_m=400)
        result = round_trip_samples(straight_chunk(backward=False), distances([10, 20], [np.inf, 600]),
            distances([10, 20], [400, np.inf]), start, 0, 1000)
        # No directed cycle connects distinct positions on this one-way segment.
        np.testing.assert_allclose(result.xy[:, 0], 375400)
        np.testing.assert_allclose(result.distances, 0)

    def test_one_way_cycle_uses_reverse_destinations(self) -> None:
        start = NumericEndpoint(segment_id=7, graph_offset_m=400)
        result = round_trip_samples(straight_chunk(backward=False), distances([10, 20], [1400, 600]),
            distances([10, 20], [400, 1200]), start, 0, 2000)
        # Remote paths close the directed cycle. All distinct positions cost 1800 m.
        away = np.abs(result.xy[:, 0] - 375400) > 1e-6
        np.testing.assert_allclose(result.distances[away], 1800)

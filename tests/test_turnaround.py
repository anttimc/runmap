"""Turnaround estimates preserve directed accessibility and width geometry."""

import math
import unittest

import numpy as np

from test_distance_shells import distances, straight_chunk
from runmap.routing.numeric_search import NumericEndpoint
from runmap.routing.turnaround import ellipse_extra_ratio, round_trip_samples


class TurnaroundTests(unittest.TestCase):
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

import math
import random
from time import monotonic
import unittest
from unittest.mock import patch

from runmap import _routing_native
from runmap.routing.types import Point
from runmap.routing.fields import integrate_fields
from runmap.routing_reference.fields import integrate_fields_python, smooth_fields


def reference_fields(points: list[Point], spacing: float) -> tuple[float, float, float]:
    radial = right = length = 0.0
    for first, second in zip(points, points[1:]):
        distance = math.dist(first, second)
        count = max(4, math.ceil(distance / spacing))
        for sample in range(count):
            fraction = (sample + 0.5) / count
            point = (first[0] + fraction * (second[0] - first[0]),
                     first[1] + fraction * (second[1] - first[1]))
            centre_field, right_field = smooth_fields(point, (3, 7), (93, 27), 15, 5)
            radial += centre_field * distance / count
            right += right_field * distance / count
        length += distance
    return radial, right, length


class FieldTests(unittest.TestCase):
    def test_integration_matches_scalar_midpoints(self) -> None:
        for points in ([], [(0, 0)], [(1, 2), (1, 2)],
                       [(2, -5), (40, 30), (40, 30), (95, -10)]):
            for spacing in (5.0, 1.0, 0.03):
                for ordered in (points, list(reversed(points))):
                    with self.subTest(points=ordered, spacing=spacing):
                        actual = integrate_fields(ordered, (3, 7), (93, 27), 15, 5, spacing)
                        expected = reference_fields(ordered, spacing)
                        for value, reference in zip(actual, expected):
                            self.assertAlmostEqual(value, reference, places=10)

    def test_expired_deadline(self) -> None:
        for distance in (10, 10000):
            with self.assertRaises(TimeoutError):
                integrate_fields([(0, 0), (distance, 0)], (0, 0), (100, 0),
                                 15, 5, deadline=monotonic() - 1)

    def test_python_reference_checks_deadline_between_batches(self) -> None:
        with patch('runmap.routing_reference.fields.monotonic', side_effect=(0, 2)):
            with self.assertRaises(TimeoutError):
                integrate_fields_python([(0, 0), (10000, 0)], (0, 0), (100, 0),
                                        15, 5, deadline=1)

    def test_native_matches_python_across_field_settings(self) -> None:
        rng = random.Random(42)
        for _ in range(40):
            points = [(rng.uniform(-100, 200), rng.uniform(-100, 200)) for _ in range(rng.randrange(2, 15))]
            for radius, width, spacing in ((15, 5, 5), (1, .1, .25), (1000, 100, 10)):
                expected = integrate_fields_python(points, (3, 7), (93, 27), radius, width, spacing)
                actual = integrate_fields(points, (3, 7), (93, 27), radius, width, spacing)
                for value, reference in zip(actual, expected):
                    self.assertTrue(math.isclose(value, reference, rel_tol=1e-12, abs_tol=1e-9),
                                    (value, reference))

    def test_wrapper_checks_deadline_after_native_call(self) -> None:
        with patch('runmap.routing.fields.monotonic', side_effect=(0, 2)):
            with self.assertRaises(TimeoutError):
                integrate_fields([(0, 0), (100, 0)], (0, 0), (100, 0), 15, 5, deadline=1)

    def test_native_stops_a_long_calculation(self) -> None:
        with self.assertRaises(TimeoutError):
            _routing_native.integrate_fields([(0, 0), (1e9, 0)], (0, 0), (100, 0),
                                             15, 5, 0.001, timeout_seconds=0.001)

    def test_native_rejects_invalid_inputs(self) -> None:
        for radius, width, spacing in ((0, 5, 5), (15, -1, 5), (15, 5, math.nan)):
            with self.assertRaises(ValueError):
                integrate_fields([(0, 0), (100, 0)], (0, 0), (100, 0), radius, width, spacing)
        with self.assertRaises(ValueError):
            integrate_fields([(math.inf, 0), (100, 0)], (0, 0), (100, 0), 15, 5)
        with self.assertRaises(ValueError):
            integrate_fields([(0, 0), (100, 0)], (0, 0), (0, 0), 15, 5)

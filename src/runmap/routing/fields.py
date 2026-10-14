"""Smooth spatial preferences shared by experiments and real-network routing."""

from collections.abc import Sequence
import math
from time import monotonic

Point = tuple[float, float]


def smooth_fields(point: Point, home: Point, destination: Point,
                  radius: float, transition_width: float) -> tuple[float, float]:
    """Keep left/right fixed in the home-to-turnaround frame for both legs."""
    centre = ((home[0] + destination[0]) / 2, (home[1] + destination[1]) / 2)
    dx, dy = destination[0] - home[0], destination[1] - home[1]
    signed = (dx * (point[1] - home[1]) - dy * (point[0] - home[0])) / math.hypot(dx, dy)
    return (math.exp(-math.dist(point, centre) ** 2 / (2 * radius ** 2)),
            (1 - math.tanh(signed / transition_width)) / 2)


def integrate_fields(points: Sequence[Point], home: Point, destination: Point,
                     radius: float, transition_width: float,
                     spacing_m: float = 5.0,
                     deadline: float | None = None) -> tuple[float, float, float]:
    """Integrate over the polyline, independent of routing junction placement."""
    radial = right = length = 0.0
    for first, second in zip(points, points[1:]):
        distance = math.dist(first, second)
        count = max(4, math.ceil(distance / spacing_m))
        for sample in range(count):
            if sample % 256 == 0 and deadline is not None and monotonic() >= deadline:
                raise TimeoutError("Field integration exceeded its deadline")
            fraction = (sample + 0.5) / count
            point = (first[0] + fraction * (second[0] - first[0]),
                     first[1] + fraction * (second[1] - first[1]))
            centre_field, right_field = smooth_fields(point, home, destination, radius, transition_width)
            radial += centre_field * distance / count
            right += right_field * distance / count
        length += distance
    return radial, right, length

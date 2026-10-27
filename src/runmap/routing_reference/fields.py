"""Python shape fields and numerical integrator for experiments and validation."""

import math, numpy as np
from collections.abc import Sequence
from time import monotonic

from numpy.typing import NDArray

from runmap.routing.types import Point


def smooth_fields_array(x: NDArray[np.float64], y: NDArray[np.float64],
                        home: Point, destination: Point, radius: float,
                        transition_width: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Evaluate a batch in the same fixed frame as the scalar fields."""
    cx, cy = (home[0] + destination[0]) / 2, (home[1] + destination[1]) / 2
    dx, dy = destination[0] - home[0], destination[1] - home[1]
    signed = (dx * (y - home[1]) - dy * (x - home[0])) / math.hypot(dx, dy)
    return (np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * radius ** 2)),
            (1 - np.tanh(signed / transition_width)) / 2)


def smooth_fields(point: Point, home: Point, destination: Point,
                  radius: float, transition_width: float) -> tuple[float, float]:
    """Keep left/right fixed in the home-to-turnaround frame for both legs."""
    centre = ((home[0] + destination[0]) / 2, (home[1] + destination[1]) / 2)
    dx, dy = destination[0] - home[0], destination[1] - home[1]
    signed = (dx * (point[1] - home[1]) - dy * (point[0] - home[0])) / math.hypot(dx, dy)
    return (math.exp(-math.dist(point, centre) ** 2 / (2 * radius ** 2)),
            (1 - math.tanh(signed / transition_width)) / 2)


def integrate_fields_python(points: Sequence[Point], home: Point, destination: Point,
                            radius: float, transition_width: float,
                            spacing_m: float = 5.0,
                            deadline: float | None = None) -> tuple[float, float, float]:
    """Integrate over the polyline, independent of routing junction placement."""
    radial = right = length = 0.0
    cx, cy = (home[0] + destination[0]) / 2, (home[1] + destination[1]) / 2
    dx, dy = destination[0] - home[0], destination[1] - home[1]
    separation = math.hypot(dx, dy)
    for first, second in zip(points, points[1:]):
        distance = math.dist(first, second)
        count = max(4, math.ceil(distance / spacing_m))
        for start in range(0, count, 256):
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError("Field integration exceeded its deadline")
            stop = min(start + 256, count)
            # Array setup costs more than scalar evaluation for short segments.
            if count >= 32:
                fractions = (np.arange(start, stop, dtype=np.float64) + 0.5) / count
                x = first[0] + fractions * (second[0] - first[0])
                y = first[1] + fractions * (second[1] - first[1])
                centre_fields, right_fields = smooth_fields_array(
                    x, y, home, destination, radius, transition_width)
                radial += float(centre_fields.sum()) * distance / count
                right += float(right_fields.sum()) * distance / count
            else:
                for sample in range(start, stop):
                    fraction = (sample + 0.5) / count
                    x = first[0] + fraction * (second[0] - first[0])
                    y = first[1] + fraction * (second[1] - first[1])
                    signed = (dx * (y - home[1]) - dy * (x - home[0])) / separation
                    radial += math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * radius ** 2)) * distance / count
                    right += (1 - math.tanh(signed / transition_width)) / 2 * distance / count
        length += distance
    return radial, right, length

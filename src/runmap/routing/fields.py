"""Scalar Rust integration adapter for reference routing and kernel benchmarks."""

from collections.abc import Sequence
from time import monotonic

from runmap import _routing_native
from runmap.routing.types import Point


def integrate_fields(points: Sequence[Point], home: Point, destination: Point,
                     radius: float, transition_width: float,
                     spacing_m: float = 10.0,
                     deadline: float | None = None) -> tuple[float, float, float]:
    """Evaluate one geometry in Rust, retaining the request's Python deadline."""
    remaining = None if deadline is None else deadline - monotonic()
    if remaining is not None and remaining <= 0:
        raise TimeoutError("Field integration exceeded its deadline")
    result = _routing_native.integrate_fields(
        points, home, destination, radius, transition_width, spacing_m, remaining)
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError("Field integration exceeded its deadline")
    return result

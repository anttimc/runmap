"""Typed signature for the scalar Rust field-integration binding."""

from collections.abc import Sequence

def integrate_fields(
    points: Sequence[tuple[float, float]],
    home: tuple[float, float],
    destination: tuple[float, float],
    radius: float,
    transition_width: float,
    spacing_m: float = 10.0,
    timeout_seconds: float | None = None,
) -> tuple[float, float, float]: ...

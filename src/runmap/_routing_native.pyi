"""Typed signatures for scalar integration and reusable native chunks."""

from collections.abc import Sequence
from runmap.paths.environment_join import EnvironmentInterval


def environment_unknown_lengths(
    values: Sequence[tuple[int, Sequence[EnvironmentInterval]]],
    lengths: Sequence[tuple[int, float]],
    spans: Sequence[tuple[int, float, float]], *, timeout_seconds: float | None = None,
) -> dict[str, bytes]: ...


class NumericChunk:
    def __init__(self, payload: bytes, *, timeout_seconds: float | None = None) -> None: ...
    @property
    def resident_bytes(self) -> int: ...
    @property
    def payload_bytes(self) -> int: ...


def integrate_fields(
    points: Sequence[tuple[float, float]],
    home: tuple[float, float],
    destination: tuple[float, float],
    radius: float,
    transition_width: float,
    spacing_m: float = 10.0,
    timeout_seconds: float | None = None,
) -> tuple[float, float, float]: ...

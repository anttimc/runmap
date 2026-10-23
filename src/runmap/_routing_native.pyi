from collections.abc import Sequence

def encode_line_tile(
    features: Sequence[tuple[Sequence[Sequence[tuple[float, float]]], dict[str, str | float | bool | int]]],
    extent: int,
) -> bytes: ...

def integrate_fields(
    points: Sequence[tuple[float, float]],
    home: tuple[float, float],
    destination: tuple[float, float],
    radius: float,
    transition_width: float,
    spacing_m: float = 5.0,
    timeout_seconds: float | None = None,
) -> tuple[float, float, float]: ...

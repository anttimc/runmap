from collections.abc import Sequence

def integrate_fields(
    points: Sequence[tuple[float, float]],
    home: tuple[float, float],
    destination: tuple[float, float],
    radius: float,
    transition_width: float,
    spacing_m: float = 5.0,
    timeout_seconds: float | None = None,
) -> tuple[float, float, float]: ...

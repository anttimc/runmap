"""Shared route-mode validation and shape-strength resolution."""

import math
from dataclasses import replace

from runmap.routing.cost_types import ShapeStrengths
from runmap.routing.run_types import RunMode, RunSettings


def effective_run_settings(settings: RunSettings, mode: RunMode) -> RunSettings:
    """One-way routes use preferences without round-trip shape costs."""
    if mode not in ("one_way", "round_trip"):
        raise ValueError("Unsupported run mode")
    validate_settings(settings)
    return replace(settings, avoidance_ratio=0) if mode == "one_way" else settings


def validate_settings(settings: RunSettings) -> None:
    values = (settings.avoidance_ratio, settings.radius_ratio, settings.transition_ratio,
              settings.side_strength, settings.reuse_strength)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Run settings must be finite")
    if not (0 <= settings.avoidance_ratio <= 1 and 0.02 <= settings.radius_ratio <= 1
            and 0.005 <= settings.transition_ratio <= 1
            and 0 <= settings.side_strength <= 100 and 0 <= settings.reuse_strength <= 100):
        raise ValueError("Run settings exceed supported ranges")
    if settings.avoidance_ratio / settings.radius_ratio > 5:
        raise ValueError("Avoidance distance must not exceed five Gaussian scales")


def resolve_shape_strengths(settings: RunSettings) -> ShapeStrengths:
    """Ease shape penalties in together, reaching full strength at 18 percent."""
    validate_settings(settings)
    fraction = min(1.0, settings.avoidance_ratio / 0.18)
    ramp = fraction * fraction * (3 - 2 * fraction)
    centre = ramp * math.exp((settings.avoidance_ratio / settings.radius_ratio) ** 2 / 2)
    return ShapeStrengths(centre if settings.centre_enabled else 0.0,
                          ramp * settings.side_strength, ramp * settings.reuse_strength)

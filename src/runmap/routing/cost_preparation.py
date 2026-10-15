"""Prepare request geometry and share lazy shape integration between searches."""

import math

from runmap.paths.exploration import Snap
from runmap.routing.cost_types import RequestCostCache, RequestCostData, ShapeFeatures, ShapeStrengths
from runmap.routing.deadlines import check_deadline
from runmap.routing.fields import integrate_fields
from runmap.routing.geometry import projected_points
from runmap.routing.run_types import RunSettings
from runmap.routing.search import GraphData
from runmap.routing.types import Arc


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


def penalty_strengths(settings: RunSettings) -> tuple[float, float, float]:
    """Preserve the original tuple interface for existing callers."""
    strengths = resolve_shape_strengths(settings)
    return strengths.centre, strengths.side, strengths.reuse


def build_request_cost_data(graph: GraphData, home: Snap, turnaround: Snap,
                            settings: RunSettings) -> RequestCostData:
    home_point, destination = projected_points([[home.longitude, home.latitude],
                                                [turnaround.longitude, turnaround.latitude]])
    separation = math.dist(home_point, destination)
    if separation < 0.01:
        raise ValueError("Home and turnaround must be spatially distinct")
    strengths = resolve_shape_strengths(settings)
    return RequestCostData(
        graph, home_point, destination, separation * settings.radius_ratio,
        separation * settings.transition_ratio,
        min(5.0, separation * min(settings.radius_ratio, settings.transition_ratio) / 4),
        strengths, settings.avoidance_ratio == 0,
    )


def ensure_arc_features(data: RequestCostData, cache: RequestCostCache,
                        arc: Arc, deadline: float | None) -> ShapeFeatures:
    """Integrate each overlay segment once for all request legs and assignments."""
    segment_id = arc["segment_id"]
    if segment_id not in cache.shape_fields:
        points = projected_points(data.graph.segments[segment_id]["geometry"])
        cache.shape_fields[segment_id] = ShapeFeatures(*integrate_fields(
            points, data.home, data.turnaround, data.radius_m,
            data.transition_width_m, data.spacing_m, deadline,
        ))
        check_deadline(deadline)
    return cache.shape_fields[segment_id]

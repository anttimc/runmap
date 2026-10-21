"""Prepare request geometry and share lazy shape integration between searches."""

import math
from collections.abc import Mapping

from runmap.paths.exploration import Snap
from runmap.routing.cost_types import (
    PhysicalPart, PreferenceSettings, RequestCostCache, RequestCostData, ShapeFeatures,
    ShapeStrengths, TraversalRef,
)
from runmap.routing.hill_types import HillData, HillFeatures
from runmap.routing.hills import indexed_traversal_hills
from runmap.routing.cost_settings import resolve_preferences
from runmap.routing.deadlines import check_deadline
from runmap.routing.fields import integrate_fields
from runmap.routing.geometry import projected_points
from runmap.routing.run_types import RunSettings
from runmap.routing.profiles import arc_forward
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
                            settings: RunSettings, *,
                            parts: Mapping[str, PhysicalPart] | None = None,
                            preferences: PreferenceSettings = PreferenceSettings(),
                            hills: HillData | None = None) -> RequestCostData:
    home_point, destination = projected_points([[home.longitude, home.latitude],
                                                [turnaround.longitude, turnaround.latitude]])
    separation = math.dist(home_point, destination)
    if separation < 0.01:
        raise ValueError("Home and turnaround must be spatially distinct")
    strengths = resolve_shape_strengths(settings)
    resolved = resolve_preferences(preferences, hills=hills)
    if resolved.weights.hills and hills is not None:
        if (hills.graph is not graph.document or graph.integer is None
                or hills.index.segment_ids is not graph.integer.segment_ids):
            raise ValueError("Hill features belong to a different prepared graph or integer ordering")
    return RequestCostData(
        graph, home_point, destination, separation * settings.radius_ratio,
        separation * settings.transition_ratio,
        min(5.0, separation * min(settings.radius_ratio, settings.transition_ratio) / 4),
        strengths, settings.avoidance_ratio == 0, parts if parts is not None else {},
        resolved, hills if resolved.weights.hills else None,
    )


def resolve_traversal(data: RequestCostData, arc: Arc) -> TraversalRef:
    """Keep physical chainage ascending and traversal direction explicit."""
    segment = data.graph.segments[arc["segment_id"]]
    part = data.parts.get(segment["id"])
    return TraversalRef(part.segment_id if part else segment["id"],
                        part.start_m if part else 0.0,
                        part.end_m if part else segment["length_m"],
                        not arc_forward(segment, arc))


def ensure_arc_features(data: RequestCostData, cache: RequestCostCache,
                        arc: Arc, deadline: float | None) -> ShapeFeatures:
    """Integrate each overlay segment once for all request legs and assignments."""
    segment_id = arc["segment_id"]
    if segment_id not in cache.shape_fields:
        points = data.graph.projected.segments[segment_id]
        cache.shape_fields[segment_id] = ShapeFeatures(*integrate_fields(
            points, data.home, data.turnaround, data.radius_m,
            data.transition_width_m, data.spacing_m, deadline,
        ))
        check_deadline(deadline)
    return cache.shape_fields[segment_id]


def ensure_hill_features(data: RequestCostData, cache: RequestCostCache,
                         arc: Arc, deadline: float | None) -> HillFeatures:
    if data.hills is None:
        return HillFeatures()
    if arc["id"] not in cache.hill_features:
        traversal = resolve_traversal(data, arc)
        segment = data.hills.index.segment_indices[traversal.segment_id]
        cache.hill_features[arc["id"]] = indexed_traversal_hills(
            data.hills, segment, traversal.graph_start_m, traversal.graph_end_m, traversal.reverse, deadline)
    return cache.hill_features[arc["id"]]

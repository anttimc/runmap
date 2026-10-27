"""Prepare request geometry and share lazy shape integration between searches."""

import math
from dataclasses import replace
from collections.abc import Mapping

from runmap.paths.exploration import Snap
from runmap.routing.cost_types import (
    PhysicalPart, PreferenceSettings, RequestCostCache, RequestCostData, ShapeFeatures,
    ShapeStrengths, TraversalRef, IndexedTraversal,
)
from runmap.routing.hill_types import HillData, HillFeatures
from runmap.routing.hills import indexed_traversal_hills
from runmap.routing.cost_settings import resolve_preferences
from runmap.routing.deadlines import check_deadline
from runmap.routing.fields import integrate_fields
from runmap.routing.geometry import projected_points, indexed_segment_points
from runmap.routing.integer_graph import IntegerArc, integer_arc, integer_segment_id
from runmap.routing.run_types import RunMode, RunSettings
from runmap.routing.search import GraphData
from runmap.routing.types import Arc


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


def penalty_strengths(settings: RunSettings) -> tuple[float, float, float]:
    """Preserve the original tuple interface for existing callers."""
    strengths = resolve_shape_strengths(settings)
    return strengths.centre, strengths.side, strengths.reuse


def build_request_cost_data(graph: GraphData, home: Snap, turnaround: Snap,
                            settings: RunSettings, *,
                            parts: Mapping[int, PhysicalPart] | None = None,
                            preferences: PreferenceSettings = PreferenceSettings(),
                            hills: HillData | None = None) -> RequestCostData:
    home_point, destination = projected_points([[home.longitude, home.latitude],
                                                [turnaround.longitude, turnaround.latitude]])
    separation = math.dist(home_point, destination)
    if separation < 0.01:
        raise ValueError("Home and turnaround must be spatially distinct")
    strengths = resolve_shape_strengths(settings)
    resolved = resolve_preferences(preferences, hills=hills)
    index = graph.integer
    if index is None:
        raise ValueError("Graph has no prepared integer index")
    if resolved.weights.hills and hills is not None:
        if (hills.graph is not graph.document or graph.integer is None
                or hills.index.segment_ids is not graph.integer.segment_ids):
            raise ValueError("Hill features belong to a different prepared graph or integer ordering")
    return RequestCostData(
        graph, index, home_point, destination, separation * settings.radius_ratio,
        separation * settings.transition_ratio,
        min(5.0, separation * min(settings.radius_ratio, settings.transition_ratio) / 4),
        strengths, settings.avoidance_ratio == 0, parts if parts is not None else {},
        resolved, hills if resolved.weights.hills else None,
    )


def resolve_traversal(data: RequestCostData, arc: Arc) -> TraversalRef:
    """Translate selected traversals to external IDs for response serialization."""
    prepared = integer_arc(data.index, data.index.arc_indices[arc["id"]])
    traversal = resolve_indexed_traversal(data, prepared)
    return TraversalRef(integer_segment_id(data.index, traversal.segment),
                        traversal.graph_start_m, traversal.graph_end_m, traversal.reverse)


def resolve_indexed_traversal(data: RequestCostData, arc: IntegerArc) -> IndexedTraversal:
    part = data.parts.get(arc.segment)
    return IndexedTraversal(part.segment if part else arc.segment,
                            part.start_m if part else 0.0,
                            part.end_m if part else arc.length_m, arc.reverse)


def ensure_arc_features(data: RequestCostData, cache: RequestCostCache,
                        arc: IntegerArc, deadline: float | None) -> ShapeFeatures:
    """Integrate each overlay segment once for all request legs and assignments."""
    segment = arc.segment
    if segment not in cache.shape_fields:
        points = indexed_segment_points(data.graph.projected, segment)
        cache.shape_fields[segment] = ShapeFeatures(*integrate_fields(
            points, data.home, data.turnaround, data.radius_m,
            data.transition_width_m, data.spacing_m, deadline,
        ))
        check_deadline(deadline)
    return cache.shape_fields[segment]


def ensure_hill_features(data: RequestCostData, cache: RequestCostCache,
                         arc: IntegerArc, deadline: float | None) -> HillFeatures:
    if data.hills is None:
        return HillFeatures()
    if arc.index not in cache.hill_features:
        traversal = resolve_indexed_traversal(data, arc)
        cache.hill_features[arc.index] = indexed_traversal_hills(
            data.hills, traversal.segment, traversal.graph_start_m, traversal.graph_end_m, traversal.reverse, deadline)
    return cache.hill_features[arc.index]

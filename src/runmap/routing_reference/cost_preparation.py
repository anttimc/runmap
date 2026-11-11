"""Reference request geometry and lazy shape and hill feature preparation."""

import math
from collections.abc import Mapping

from runmap.paths.exploration import Snap
from runmap.paths.environment_join import EnvironmentInterval
from runmap.routing.environment_costs import EnvironmentCost, EnvironmentWeights, evaluate_environment_cost
from runmap.routing.cost_settings import resolve_preferences
from runmap.routing.cost_types import PreferenceSettings, TraversalRef
from runmap.routing.deadlines import check_deadline
from runmap.routing.fields import integrate_fields
from runmap.routing.geometry import indexed_segment_points, projected_points
from runmap.routing.graph import GraphData
from runmap.routing.hill_types import HillData, HillFeatures
from runmap.routing.hills import indexed_traversal_hills
from runmap.routing.integer_graph import IntegerArc, integer_arc, integer_segment_id
from runmap.routing.run_types import RunSettings
from runmap.routing.settings import resolve_shape_strengths
from runmap.routing.types import Arc
from runmap.routing.signals import SignalData
from runmap.routing_reference.cost_types import (
    IndexedTraversal,
    PhysicalPart,
    RequestCostCache,
    RequestCostData,
    ShapeFeatures,
)


def penalty_strengths(settings: RunSettings) -> tuple[float, float, float]:
    """Preserve the original tuple interface for existing callers."""
    strengths = resolve_shape_strengths(settings)
    return strengths.centre, strengths.side, strengths.reuse


def _prepare_environment(graph: GraphData, environment: Mapping[str, tuple[EnvironmentInterval, ...]] | None,
                         deadline: float | None
                         ) -> dict[int, tuple[EnvironmentInterval, ...]] | None:
    if environment is None:
        return None
    assert graph.integer is not None
    prepared: dict[int, tuple[EnvironmentInterval, ...]] = {}
    for segment, key in enumerate(graph.integer.segment_ids):
        check_deadline(deadline)
        intervals = environment.get(key)
        if not intervals or any(item.segment_id != key for item in intervals):
            raise ValueError("Environment join lacks a matching physical segment")
        length = graph.segments[key]["length_m"]
        if not math.isclose(intervals[-1].graph_end_m, length, rel_tol=0, abs_tol=1e-6):
            raise ValueError("Environment chainage does not match graph")
        evaluate_environment_cost(intervals, 0, length, EnvironmentWeights())
        prepared[segment] = intervals
    return prepared


def build_request_cost_data(graph: GraphData, home: Snap, turnaround: Snap,
                            settings: RunSettings, *,
                            parts: Mapping[int, PhysicalPart] | None = None,
                            preferences: PreferenceSettings = PreferenceSettings(),
                            hills: HillData | None = None,
                            environment: Mapping[str, tuple[EnvironmentInterval, ...]] | None = None,
                            environment_weights: EnvironmentWeights = EnvironmentWeights(),
                            signals: SignalData | None = None,
                            deadline: float | None = None) -> RequestCostData:
    home_point, destination = projected_points([[home.longitude, home.latitude],
                                                [turnaround.longitude, turnaround.latitude]])
    separation = math.dist(home_point, destination)
    if separation < 0.01:
        raise ValueError("Home and turnaround must be spatially distinct")
    strengths = resolve_shape_strengths(settings)
    resolved = resolve_preferences(preferences, hills=hills, signals=signals is not None)
    index = graph.integer
    if index is None:
        raise ValueError("Graph has no prepared integer index")
    strengths_environment = (environment_weights.greenery, environment_weights.water,
                             environment_weights.traffic, environment_weights.wetland)
    if any(not math.isfinite(value) or value < 0 for value in strengths_environment):
        raise ValueError("Environment weights must be finite and nonnegative")
    if any(strengths_environment) and environment is None:
        raise ValueError("Environment weights require prepared intervals")
    prepared_environment = _prepare_environment(graph, environment, deadline)
    if resolved.weights.hills and hills is not None:
        if (hills.graph is not graph.document or graph.integer is None
                or hills.index.segment_ids is not graph.integer.segment_ids):
            raise ValueError("Hill features belong to a different prepared graph or integer ordering")
    return RequestCostData(
        graph, index, home_point, destination, separation * settings.radius_ratio,
        separation * settings.transition_ratio,
        min(10.0, separation * min(settings.radius_ratio, settings.transition_ratio) / 4),
        strengths, settings.avoidance_ratio == 0, parts if parts is not None else {},
        resolved, hills if resolved.weights.hills else None, prepared_environment, environment_weights, signals,
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


def ensure_environment_cost(data: RequestCostData, cache: RequestCostCache,
                            arc: IntegerArc, deadline: float | None) -> EnvironmentCost | None:
    """Use physical chainage for overlay arcs and cache every directed passage."""
    if data.environment is None:
        return None
    if arc.index not in cache.environment_costs:
        check_deadline(deadline)
        traversal = resolve_indexed_traversal(data, arc)
        cache.environment_costs[arc.index] = evaluate_environment_cost(
            data.environment[traversal.segment], traversal.graph_start_m, traversal.graph_end_m,
            data.environment_weights)
    return cache.environment_costs[arc.index]

"""Shape-cost formulas and the adapter used by generic graph search."""

import math
from collections.abc import Iterable

from runmap.routing.cost_preparation import ensure_arc_features, ensure_hill_features
from runmap.routing.cost_types import (
    CostBreakdown, LegCostContext, PreferenceCosts, RequestCostCache, RequestCostData,
    ShapeFeatures, ShapeStrengths, PreferenceObservations,
)
from runmap.routing.hills import hill_observations, sum_hill_features
from runmap.routing.deadlines import check_deadline
from runmap.routing.types import Arc
from runmap.routing.integer_graph import IntegerArc, integer_arc


NO_SHAPE_FEATURES = ShapeFeatures(0, 0, 0)


def centre_penalty(features: ShapeFeatures, strengths: ShapeStrengths) -> float:
    return strengths.centre * features.radial_integral_m


def side_penalty(features: ShapeFeatures, strengths: ShapeStrengths,
                 penalize_right: bool) -> float:
    side = (features.right_integral_m if penalize_right
            else features.projected_length_m - features.right_integral_m)
    return strengths.side * max(0.0, side)


def reuse_penalty(length_m: float, strengths: ShapeStrengths, reused: bool) -> float:
    return strengths.reuse * length_m * reused


def evaluate_arc_cost(length_m: float, features: ShapeFeatures,
                      strengths: ShapeStrengths, leg: LegCostContext,
                      reused: bool, preferences: PreferenceCosts = PreferenceCosts()) -> float:
    return _cost_total(length_m, centre_penalty(features, strengths),
                       side_penalty(features, strengths, leg.penalize_right),
                       reuse_penalty(length_m, strengths, reused), preferences)


def _cost_total(length_m: float, centre: float, side: float, reuse: float,
                preferences: PreferenceCosts) -> float:
    components = (length_m, centre, side, reuse, preferences.hills, preferences.signals,
                  preferences.busy_roads, preferences.non_green)
    if any(not math.isfinite(value) or value < 0 for value in components):
        raise ValueError("Cost components must be finite and nonnegative")
    total = (length_m + centre + side + reuse + preferences.hills + preferences.signals
             + preferences.busy_roads + preferences.non_green)
    if not math.isfinite(total) or total < length_m:
        raise ValueError("Total cost must be finite and at least physical length")
    return total


def evaluate_arc_breakdown(length_m: float, features: ShapeFeatures,
                           strengths: ShapeStrengths, leg: LegCostContext,
                           reused: bool, preferences: PreferenceCosts = PreferenceCosts()) -> CostBreakdown:
    centre = centre_penalty(features, strengths)
    side = side_penalty(features, strengths, leg.penalize_right)
    reuse = reuse_penalty(length_m, strengths, reused)
    total = _cost_total(length_m, centre, side, reuse, preferences)
    return CostBreakdown(length_m, centre, side, reuse, preferences.hills, preferences.signals,
                         preferences.busy_roads, preferences.non_green, total)


def path_cost_breakdown(arc_ids: Iterable[str], data: RequestCostData,
                        cache: RequestCostCache, leg: LegCostContext) -> CostBreakdown:
    """Explain selected paths using cached features and the original search context."""
    costs: list[CostBreakdown] = []
    for arc_id in arc_ids:
        arc = integer_arc(data.index, data.index.arc_indices[arc_id])
        features = NO_SHAPE_FEATURES if data.shape_disabled else cache.shape_fields[arc.segment]
        preferences = (PreferenceCosts(hills=cache.hill_features[arc.index].penalty_m * data.preferences.weights.hills)
                       if data.hills else PreferenceCosts())
        costs.append(evaluate_arc_breakdown(arc.length_m, features, data.strengths, leg,
                                            arc.segment in leg.used_segments, preferences))
    return CostBreakdown(
        sum(c.distance_m for c in costs), sum(c.centre for c in costs),
        sum(c.side for c in costs), sum(c.reuse for c in costs), sum(c.hills for c in costs),
        sum(c.signals for c in costs), sum(c.busy_roads for c in costs),
        sum(c.non_green for c in costs), sum(c.total for c in costs),
    )


def path_observations(arc_ids: Iterable[str], data: RequestCostData,
                      cache: RequestCostCache) -> PreferenceObservations:
    if data.hills is None:
        return PreferenceObservations()
    return hill_observations(sum_hill_features(cache.hill_features[data.index.arc_indices[arc_id]] for arc_id in arc_ids))


def arc_cost(arc: Arc, *, data: RequestCostData, cache: RequestCostCache,
             leg: LegCostContext, deadline: float | None) -> float:
    """Adapt a legacy record callback at the boundary of indexed cost evaluation."""
    return indexed_arc_cost(integer_arc(data.index, data.index.arc_indices[arc["id"]]),
                             data=data, cache=cache, leg=leg, deadline=deadline)


def indexed_arc_cost(arc: IntegerArc, *, data: RequestCostData, cache: RequestCostCache,
                     leg: LegCostContext, deadline: float | None) -> float:
    """Evaluate prepared arcs without string lookup or JSON-record access."""
    check_deadline(deadline)
    features = NO_SHAPE_FEATURES if data.shape_disabled else ensure_arc_features(data, cache, arc, deadline)
    reused = arc.segment in leg.used_segments
    if data.hills is None:
        return evaluate_arc_cost(arc.length_m, features, data.strengths, leg, reused)
    hills = ensure_hill_features(data, cache, arc, deadline)
    return evaluate_arc_cost(arc.length_m, features, data.strengths, leg, reused,
                             PreferenceCosts(hills=hills.penalty_m * data.preferences.weights.hills))

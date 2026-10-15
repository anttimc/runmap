"""Shape-cost formulas and the adapter used by generic graph search."""

from runmap.routing.cost_preparation import ensure_arc_features
from runmap.routing.cost_types import (
    LegCostContext, RequestCostCache, RequestCostData, ShapeFeatures, ShapeStrengths,
)
from runmap.routing.deadlines import check_deadline
from runmap.routing.types import Arc


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
                      reused: bool) -> float:
    return (length_m + centre_penalty(features, strengths)
            + side_penalty(features, strengths, leg.penalize_right)
            + reuse_penalty(length_m, strengths, reused))


def arc_cost(arc: Arc, *, data: RequestCostData, cache: RequestCostCache,
             leg: LegCostContext, deadline: float | None) -> float:
    """Bind explicit request data to the search's scalar arc-cost interface."""
    check_deadline(deadline)
    if data.distance_only:
        return arc["length_m"]
    features = ensure_arc_features(data, cache, arc, deadline)
    return evaluate_arc_cost(arc["length_m"], features, data.strengths, leg,
                             arc["segment_id"] in leg.used_segments)

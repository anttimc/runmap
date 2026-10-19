"""Minetti running energy integrated over the original sampled physical spans."""

import math
from collections.abc import Iterable, Sequence
from types import MappingProxyType

from runmap.paths.measurements import validate_measurements
from runmap.routing.cost_types import PreferenceObservations, TraversalRef
from runmap.routing.deadlines import check_deadline
from runmap.routing.geometry import projected_points
from runmap.routing.hill_types import HillData, HillFeatures, HillInterval, HillModel
from runmap.routing.types import GraphDocument, Measurement, MeasurementDocument, Segment


def _polynomial(value: float, coefficients: Sequence[float]) -> float:
    result = 0.0
    for coefficient in coefficients:
        result = result * value + coefficient
    return result


def running_energy_cost(grade_percent: float, model: HillModel = HillModel()) -> float:
    """Evaluate Figure 1's Cr fit (J/kg/m), with tangent tails outside ±45%."""
    if not math.isfinite(grade_percent):
        raise ValueError("Hill grade must be finite")
    grade = grade_percent / 100
    boundary = min(model.fitted_grade_limit, max(-model.fitted_grade_limit, grade))
    energy = _polynomial(boundary, model.coefficients)
    if boundary != grade:
        degree = len(model.coefficients) - 1
        derivative = tuple(coefficient * (degree - index)
                           for index, coefficient in enumerate(model.coefficients[:-1]))
        energy += (grade - boundary) * _polynomial(boundary, derivative)
    if not math.isfinite(energy) or energy <= 0:
        raise ValueError("Running energy must be positive and finite")
    return energy


def running_energy_response(grade_percent: float, model: HillModel = HillModel()) -> float:
    """Convert projected distance to flat-equivalent running energy distance."""
    response = (running_energy_cost(grade_percent, model) / model.coefficients[-1]
                * math.hypot(1, grade_percent / 100))
    if not math.isfinite(response):
        raise ValueError("Hill response must be finite")
    return response


def grade_response(grade_percent: float, model: HillModel = HillModel()) -> float:
    """Favor sustained gentle climbs over short steep climbs."""
    ratio = running_energy_cost(grade_percent, model) / model.coefficients[-1]
    if grade_percent > 0:
        ratio = 1 + (ratio - 1) * (grade_percent / model.uphill_reference_grade_percent) ** model.uphill_grade_power
    response = ratio * math.hypot(1, grade_percent / 100)
    if not math.isfinite(response):
        raise ValueError("Hill response must be finite")
    return response


def hill_penalty_response(grade_percent: float, model: HillModel = HillModel()) -> float:
    """Charge excess over flat effort without rewarding downhill detours."""
    return max(0.0, grade_response(grade_percent, model) - 1.0)


def _uncertain_response(model: HillModel) -> float:
    """Use unadjusted excess effort when the actual slope is unknown."""
    return max(0.0, running_energy_response(model.uncertain_grade_percent, model) - 1.0)


def sum_hill_features(features: Iterable[HillFeatures]) -> HillFeatures:
    items = tuple(features)
    return HillFeatures(sum(i.penalty_m for i in items), sum(i.ascent_m for i in items),
                        sum(i.descent_m for i in items), sum(i.valid_length_m for i in items),
                        sum(i.trusted_length_m for i in items), sum(i.fallback_length_m for i in items),
                        sum(i.length_m for i in items))


def _scaled(features: HillFeatures, fraction: float) -> HillFeatures:
    return HillFeatures(features.penalty_m * fraction, features.ascent_m * fraction,
                        features.descent_m * fraction, features.valid_length_m * fraction,
                        features.trusted_length_m * fraction, features.fallback_length_m * fraction,
                        features.length_m * fraction)


def _interval_features(item: Measurement, reverse: bool, model: HillModel) -> HillFeatures:
    length = item["end_m"] - item["start_m"]
    grade, height = item["grade_percent"], item["height_change_m"]
    valid = item["valid"] and grade is not None and height is not None
    if grade is not None and height is not None and valid and (not math.isfinite(grade) or not math.isfinite(height)):
        raise ValueError("Valid hill measurements must have finite grade and height")
    trusted = valid and not {"bridge", "tunnel", "missing_terrain"}.intersection(item["quality_flags"])
    signed_grade = (-grade if reverse else grade) if grade is not None else 0.0
    change = (-height if reverse else height) if height is not None and valid else 0.0
    response = (hill_penalty_response(signed_grade, model) if trusted
                else _uncertain_response(model))
    return HillFeatures(length * response, max(0.0, change), max(0.0, -change),
                        length if valid else 0.0, length if trusted else 0.0,
                        0.0 if trusted else length, length)


def _segment_intervals(items: Sequence[Measurement], graph_length_m: float,
                        model: HillModel, fallback_length_m: float) -> tuple[HillInterval, ...]:
    """Reject gaps so absent terrain cannot silently become a free interval."""
    if not math.isfinite(graph_length_m) or graph_length_m <= 0:
        raise ValueError("Hill segment length must be positive and finite")
    if not items:
        if not math.isfinite(fallback_length_m) or fallback_length_m <= 0:
            raise ValueError("Unmeasured hill geometry must have positive projected length")
        fallback = HillFeatures(fallback_length_m * _uncertain_response(model),
                                fallback_length_m=fallback_length_m, length_m=fallback_length_m)
        return (HillInterval(0, graph_length_m, fallback, fallback),)
    result: list[HillInterval] = []
    graph_end = projected_end = 0.0
    for item in items:
        values = (item["graph_start_m"], item["graph_end_m"], item["start_m"], item["end_m"])
        if (not all(math.isfinite(v) for v in values) or item["graph_end_m"] <= item["graph_start_m"]
                or item["end_m"] <= item["start_m"]
                or not math.isclose(item["graph_start_m"], graph_end, abs_tol=1e-6)
                or not math.isclose(item["start_m"], projected_end, abs_tol=1e-6)):
            raise ValueError("Hill measurement chainage must be contiguous and increasing")
        result.append(HillInterval(item["graph_start_m"], item["graph_end_m"],
                                   _interval_features(item, False, model), _interval_features(item, True, model)))
        graph_end, projected_end = item["graph_end_m"], item["end_m"]
    if not math.isclose(graph_end, graph_length_m, abs_tol=1e-6):
        raise ValueError("Hill measurements must cover the physical segment")
    return tuple(result)


def _prepare_segment(segment: Segment, items: Sequence[Measurement], model: HillModel) -> tuple[HillInterval, ...]:
    fallback_length = segment["length_m"]
    if not items:
        points = projected_points(segment["geometry"])
        fallback_length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    return _segment_intervals(items, segment["length_m"], model, fallback_length)


def build_hill_data(graph: GraphDocument, document: MeasurementDocument) -> HillData:
    """Prepare unweighted responses once from the existing validated terrain artifact."""
    validate_measurements(graph, document)
    model = HillModel()
    segments = {segment["id"]: _prepare_segment(segment, document["segments"][segment["id"]], model)
                for segment in graph["segments"]}
    totals = sum_hill_features(item.forward for items in segments.values() for item in items)
    return HillData(graph, document["graph_digest"], model, MappingProxyType(segments),
                    totals.valid_length_m / totals.length_m if totals.length_m else 0.0,
                    totals.trusted_length_m / totals.length_m if totals.length_m else 0.0)


def traversal_hills(data: HillData, traversal: TraversalRef, deadline: float | None = None) -> HillFeatures:
    intervals = data.segments[traversal.segment_id]
    if not 0 <= traversal.graph_start_m < traversal.graph_end_m <= intervals[-1].graph_end_m + 1e-6:
        raise ValueError("Hill traversal is outside its physical segment")
    pieces: list[HillFeatures] = []
    for index, item in enumerate(intervals):
        if index % 256 == 0:
            check_deadline(deadline)
        start = max(item.graph_start_m, traversal.graph_start_m)
        end = min(item.graph_end_m, traversal.graph_end_m)
        if end > start:
            fraction = (end - start) / (item.graph_end_m - item.graph_start_m)
            pieces.append(_scaled(item.backward if traversal.reverse else item.forward, fraction))
    return sum_hill_features(pieces)


def hill_observations(features: HillFeatures) -> PreferenceObservations:
    length = features.length_m
    return PreferenceObservations(
        estimated_ascent_m=features.ascent_m if features.valid_length_m else None,
        estimated_descent_m=features.descent_m if features.valid_length_m else None,
        terrain_valid_fraction=features.valid_length_m / length if length else None,
        terrain_trusted_fraction=features.trusted_length_m / length if length else None,
        terrain_fallback_distance_m=features.fallback_length_m,
    )

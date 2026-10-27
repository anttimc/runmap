"""Prepare and inspect directional hill-effort features from measured path intervals."""

import math
from collections.abc import Iterable, Sequence

import numpy as np
from numpy.typing import NDArray

from runmap.paths.measurements import MeasurementData, segment_measurements, validate_measurements
from runmap.routing.cost_types import PreferenceObservations, TraversalRef
from runmap.routing.deadlines import check_deadline
from runmap.routing.geometry import projected_points
from runmap.routing.hill_types import HillData, HillFeatures, HillInterval, HillModel
from runmap.routing.graph import GraphData
from runmap.routing.types import Measurement, MeasurementDocument, Segment


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


def _feature_values(features: HillFeatures) -> tuple[float, ...]:
    return (features.penalty_m, features.ascent_m, features.descent_m, features.valid_length_m,
            features.trusted_length_m, features.fallback_length_m, features.length_m)


def _feature_record(values: NDArray[np.float64]) -> HillFeatures:
    return HillFeatures(*(float(value) for value in values))


def _fill_segment(data: HillData, segment: int, intervals: Sequence[HillInterval]) -> None:
    offset = int(data.interval_offsets[segment])
    for number, item in enumerate(intervals, offset):
        data.chainage[number] = item.graph_start_m, item.graph_end_m
        data.features[number, 0] = _feature_values(item.forward)
        data.features[number, 1] = _feature_values(item.backward)
    data.totals[segment, 0] = _feature_values(sum_hill_features(item.forward for item in intervals))
    data.totals[segment, 1] = _feature_values(sum_hill_features(item.backward for item in intervals))


def build_hill_data(graph: GraphData, document: MeasurementDocument | MeasurementData) -> HillData:
    """Share the prepared segment IDs and retain only packed numeric hill data."""
    if not isinstance(document, MeasurementData):
        validate_measurements(graph.document, document)
    index = graph.integer
    if index is None or index.extra_node_ids or index.extra_segment_ids or index.extra_arcs or index.arc_overrides:
        raise ValueError("Hill preparation requires the base integer graph")
    if isinstance(document, MeasurementData) and (document.graph is not graph.document
                                                 or document.segment_ids is not index.segment_ids):
        raise ValueError("Hill measurements must share the graph segment ordering")
    model = HillModel()
    offsets = np.empty(len(index.segment_ids) + 1, dtype=np.int64)
    offsets[0] = 0
    for segment, segment_id in enumerate(index.segment_ids):
        count = (int(document.interval_offsets[segment + 1] - document.interval_offsets[segment])
                 if isinstance(document, MeasurementData) else len(document["segments"][segment_id]))
        offsets[segment + 1] = offsets[segment] + max(1, count)
    count = int(offsets[-1])
    digest = document.graph_digest if isinstance(document, MeasurementData) else document["graph_digest"]
    data = HillData(graph.document, digest, model, index, offsets,
                    np.empty((count, 2), dtype=np.float64), np.empty((count, 2, 7), dtype=np.float64),
                    np.empty((len(index.segment_ids), 2, 7), dtype=np.float64), 0.0, 0.0)
    for segment, segment_id in enumerate(index.segment_ids):
        items = (segment_measurements(document, segment_id) if isinstance(document, MeasurementData)
                 else document["segments"][segment_id])
        intervals = _prepare_segment(graph.segments[segment_id], items, model)
        _fill_segment(data, segment, intervals)
    length = sum(float(value) for value in data.features[:, 0, 6])
    valid = sum(float(value) for value in data.features[:, 0, 3])
    trusted = sum(float(value) for value in data.features[:, 0, 4])
    for array in (data.interval_offsets, data.chainage, data.features, data.totals):
        array.flags.writeable = False
    return HillData(data.graph, data.graph_digest, model, index, offsets, data.chainage, data.features,
                    data.totals, valid / length if length else 0.0, trusted / length if length else 0.0)


def traversal_hills(data: HillData, traversal: TraversalRef, deadline: float | None = None) -> HillFeatures:
    """Translate an external traversal at the boundary of the indexed hill store."""
    return indexed_traversal_hills(data, data.index.segment_indices[traversal.segment_id],
                                   traversal.graph_start_m, traversal.graph_end_m, traversal.reverse, deadline)


def indexed_traversal_hills(data: HillData, segment: int, start_m: float, end_m: float,
                           reverse: bool, deadline: float | None = None) -> HillFeatures:
    """Read segment totals directly; inspect intervals only for partial spans."""
    check_deadline(deadline)
    if not 0 <= segment < len(data.index.segment_ids):
        raise ValueError("Hill segment index is outside the prepared graph")
    lo, hi = int(data.interval_offsets[segment]), int(data.interval_offsets[segment + 1])
    length = float(data.chainage[hi - 1, 1])
    if not 0 <= start_m < end_m <= length + 1e-6:
        raise ValueError("Hill traversal is outside its physical segment")
    direction = int(reverse)
    if start_m == 0 and end_m == length:
        return _feature_record(data.totals[segment, direction])
    chainage = data.chainage[lo:hi]
    first = lo + int(np.searchsorted(chainage[:, 1], start_m, side="right"))
    last = lo + int(np.searchsorted(chainage[:, 0], end_m, side="left"))
    pieces: list[HillFeatures] = []
    for number in range(first, last):
        if (number - first) % 256 == 0:
            check_deadline(deadline)
        a, b = data.chainage[number]
        start, end = max(float(a), start_m), min(float(b), end_m)
        if end > start:
            fraction = (end - start) / float(b - a)
            pieces.append(_scaled(_feature_record(data.features[number, direction]), fraction))
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

"""Estimate turnaround length and empirical spread from directed network distances."""

import math

import numpy as np

from runmap.paths.numeric_format import NumericChunk
from runmap.routing.distance_shells import (
    DistanceSamples, FloatArray, _geometry_samples, _segment_anchors,
)
from runmap.routing.numeric_search import NumericDistances, NumericEndpoint
from runmap.routing.turnaround_calibration import COEFFICIENTS, RESIDUAL_KNOTS, WIDTH_SCALE

TARGET_TOLERANCE = .05


def validate_width(width: float) -> None:
    if not math.isfinite(width) or not 0 <= width <= WIDTH_SCALE:
        raise ValueError("Width must be between 0 and 0.6")


def cubic_extra_ratio(width: float) -> float:
    """Preserve the shortest-path anchor while modeling the planner's width response."""
    validate_width(width)
    q = width / WIDTH_SCALE
    linear, quadratic, cubic = COEFFICIENTS
    return q * (linear + q * (quadratic + q * cubic))


def width_residual_interval(width: float) -> tuple[float, float]:
    """Interpolate empirical route-level residuals in units of start/turnaround separation."""
    validate_width(width)
    knots = np.asarray(RESIDUAL_KNOTS)
    return float(np.interp(width, knots[:, 0], knots[:, 1])), float(np.interp(width, knots[:, 0], knots[:, 2]))


def target_region_score(baseline: FloatArray, separation: FloatArray, target_m: float,
                        width: float) -> FloatArray:
    """Contour the target core at 0–1 and its empirical uncertainty fringe at 1–2."""
    predicted = baseline + separation * cubic_extra_ratio(width)
    lower_error, upper_error = width_residual_interval(width)
    low, high = target_m * (1 - TARGET_TOLERANCE), target_m * (1 + TARGET_TOLERANCE)
    score = np.abs(predicted - target_m) / (target_m * TARGET_TOLERANCE)
    if width > 0:
        below = 1 + (low - predicted) / np.maximum(separation * upper_error, 1e-9)
        above = 1 + (predicted - high) / np.maximum(-separation * lower_error, 1e-9)
        score = np.where(predicted < low, below, np.where(predicted > high, above, score))
    return np.where(baseline <= high, score, np.nan)


def ellipse_extra_ratio(width: float) -> float:
    """Retain the historical ellipse comparator for offline experiments."""
    if width == 0:
        return 0.0
    a, b = 0.5, width
    h = ((a - b) / (a + b)) ** 2
    return max(0.0, math.pi * (a + b) * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h))) - 2)


def round_trip_samples(chunk: NumericChunk, outward: NumericDistances, returning: NumericDistances,
                       start: NumericEndpoint, profile: int, maximum_m: float) -> DistanceSamples:
    """Evaluate both legal directions at identical geometry positions, including partial starts."""
    anchors = [_segment_anchors(chunk, distances, profile, reverse_search=reverse)
               for distances, reverse in ((outward, False), (returning, True))]
    start_segments = chunk.arrays["segment_ids"] == start.segment_id
    forward, backward, lengths, _ = anchors[0]
    selected = (np.minimum(forward, backward) <= maximum_m) | start_segments
    xy, segments, chainage, totals = _geometry_samples(chunk, selected, 25)
    positions = chainage * np.divide(lengths[segments], totals[segments],
                                     out=np.zeros(len(segments)), where=totals[segments] > 0)
    legs = []
    for forward, backward, lengths, directions in anchors:
        values = np.minimum(forward[segments] + positions, backward[segments] + lengths[segments] - positions)
        if start.segment_id is not None:
            delta = positions - start.graph_offset_m
            direct = start_segments[segments] & np.where(delta >= 0, directions[segments, 0], directions[segments, 1])
            values = np.minimum(values, np.where(direct, np.abs(delta), np.inf))
        legs.append(values)
    baseline = legs[0] + legs[1]
    reached = np.isfinite(baseline)
    return DistanceSamples(xy[reached], baseline[reached])

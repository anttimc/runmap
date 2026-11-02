"""Estimate turnaround regions from directed round-trip distances and ellipse geometry."""

import math

import numpy as np

from runmap.paths.numeric_format import NumericChunk
from runmap.routing.distance_shells import (
    DistanceSamples, _geometry_samples, _segment_anchors,
)
from runmap.routing.numeric_search import NumericDistances, NumericEndpoint


def ellipse_extra_ratio(width: float) -> float:
    """Use Ramanujan's perimeter estimate, with an exact out-and-back limit."""
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

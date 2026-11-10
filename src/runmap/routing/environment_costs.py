"""Evaluate length-equivalent environment penalties on joined segment intervals."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from runmap.environment_fields import EnvironmentField, EnvironmentLengths, FIELD_DEFINITIONS
from runmap.paths.environment_join import EnvironmentInterval

COST_MODEL = "mapped-context-environment-cost-v1"
FIELD_NAMES = tuple(field.value for field in EnvironmentField)


@dataclass(frozen=True)
class EnvironmentWeights:
    greenery: float = 0.0
    water: float = 0.0
    traffic: float = 0.0
    wetland: float = 0.0


@dataclass(frozen=True)
class EnvironmentCost:
    greenery_m: float
    water_m: float
    traffic_m: float
    wetland_m: float
    unknown_graph_m: EnvironmentLengths


def _weights(weights: EnvironmentWeights) -> dict[EnvironmentField, float]:
    values = {field: getattr(weights, field) for field in EnvironmentField}
    if any(not math.isfinite(value) or value < 0 for value in values.values()):
        raise ValueError("Environment weights must be finite and nonnegative")
    return values


def _valid_interval(interval: EnvironmentInterval, previous: float) -> None:
    if (not math.isfinite(interval.graph_start_m) or not math.isfinite(interval.graph_end_m)
            or interval.graph_end_m <= interval.graph_start_m
            or not math.isclose(interval.graph_start_m, previous, abs_tol=1e-6)
            or not math.isfinite(interval.projected_length_m) or interval.projected_length_m <= 0):
        raise ValueError("Environment intervals must cover contiguous positive chainage")
    for value in (*interval.values, *interval.support):
        if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError("Environment values and support must lie in [0, 1]")


def _supported(interval: EnvironmentInterval, field: EnvironmentField) -> bool:
    value = getattr(interval.values, field)
    return (value is not None and interval.support.context_support == 1
            and (not FIELD_DEFINITIONS[field].requires_traffic_support
                 or interval.support.traffic_context_support == 1))


def evaluate_environment_cost(intervals: Sequence[EnvironmentInterval], start_m: float,
                              end_m: float, weights: EnvironmentWeights) -> EnvironmentCost:
    """Integrate one physical traversal; unsupported metres get maximum penalty."""
    strengths = _weights(weights)
    if (not intervals or not math.isfinite(start_m) or not math.isfinite(end_m)
            or start_m < 0 or end_m <= start_m):
        raise ValueError("Invalid environment traversal span")
    segment_id = intervals[0].segment_id
    previous = 0.0
    for interval in intervals:
        if interval.segment_id != segment_id:
            raise ValueError("Environment intervals mix physical segments")
        _valid_interval(interval, previous)
        previous = interval.graph_end_m
    if end_m > previous + 1e-6:
        raise ValueError("Environment traversal exceeds prepared segment length")
    penalties: dict[EnvironmentField, list[float]] = {field: [] for field in EnvironmentField}
    unknown: dict[EnvironmentField, list[float]] = {field: [] for field in EnvironmentField}
    for interval in intervals:
        length = max(0.0, min(end_m, interval.graph_end_m) - max(start_m, interval.graph_start_m))
        if length <= 0:
            continue
        for field, weight in strengths.items():
            supported = _supported(interval, field)
            if not supported:
                unknown[field].append(length)
            if weight == 0:
                continue
            value = getattr(interval.values, field)
            unit_penalty = 1.0
            if supported and value is not None:
                unit_penalty = 1 - value if FIELD_DEFINITIONS[field].prefer_high else value
            penalties[field].append(weight * length * unit_penalty)
    costs = {field: math.fsum(values) for field, values in penalties.items()}
    if any(not math.isfinite(value) or value < 0 for value in costs.values()):
        raise ValueError("Invalid environment cost")
    return EnvironmentCost(
        **{f"{field}_m": cost for field, cost in costs.items()},
        unknown_graph_m=EnvironmentLengths(**{field: math.fsum(values) for field, values in unknown.items()}),
    )


def sum_environment_costs(costs: Iterable[EnvironmentCost]) -> EnvironmentCost:
    """Count every route passage, including repeated or reverse traversals."""
    records = tuple(costs)
    return EnvironmentCost(
        **{f"{field}_m": math.fsum(getattr(record, f"{field}_m") for record in records)
           for field in EnvironmentField},
        unknown_graph_m=EnvironmentLengths(**{
            field: math.fsum(getattr(record.unknown_graph_m, field) for record in records)
            for field in EnvironmentField}),
    )


def total_environment_penalty(cost: EnvironmentCost) -> float:
    """Return the nonnegative increment to the existing route objective."""
    return math.fsum(getattr(cost, f"{field}_m") for field in EnvironmentField)

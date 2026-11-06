"""Evaluate length-equivalent environment penalties on joined segment intervals."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from runmap.paths.environment_join import EnvironmentInterval

COST_MODEL = "mapped-context-environment-cost-v1"
FIELD_NAMES = ("greenery", "water", "traffic", "wetland")


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
    unknown_graph_m: tuple[float, float, float, float]


def _weights(weights: EnvironmentWeights) -> tuple[float, float, float, float]:
    values = (weights.greenery, weights.water, weights.traffic, weights.wetland)
    if any(not math.isfinite(value) or value < 0 for value in values):
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


def _supported(interval: EnvironmentInterval, field: int) -> bool:
    value = interval.values[field]
    return (value is not None and interval.support[0] == 1
            and (field != 2 or interval.support[1] == 1))


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
    penalties: list[list[float]] = [[] for _ in FIELD_NAMES]
    unknown: list[list[float]] = [[] for _ in FIELD_NAMES]
    for interval in intervals:
        length = max(0.0, min(end_m, interval.graph_end_m) - max(start_m, interval.graph_start_m))
        if length <= 0:
            continue
        for field, weight in enumerate(strengths):
            supported = _supported(interval, field)
            if not supported:
                unknown[field].append(length)
            if weight == 0:
                continue
            value = interval.values[field]
            unit_penalty = ((1 - value) if field < 2 else value) if supported and value is not None else 1.0
            penalties[field].append(weight * length * unit_penalty)
    costs = [math.fsum(values) for values in penalties]
    if any(not math.isfinite(value) or value < 0 for value in costs):
        raise ValueError("Invalid environment cost")
    unknown_lengths = [math.fsum(values) for values in unknown]
    return EnvironmentCost(
        greenery_m=costs[0], water_m=costs[1], traffic_m=costs[2], wetland_m=costs[3],
        unknown_graph_m=(unknown_lengths[0], unknown_lengths[1], unknown_lengths[2], unknown_lengths[3]),
    )


def sum_environment_costs(costs: Iterable[EnvironmentCost]) -> EnvironmentCost:
    """Count every route passage, including repeated or reverse traversals."""
    records = tuple(costs)
    return EnvironmentCost(
        greenery_m=math.fsum(record.greenery_m for record in records),
        water_m=math.fsum(record.water_m for record in records),
        traffic_m=math.fsum(record.traffic_m for record in records),
        wetland_m=math.fsum(record.wetland_m for record in records),
        unknown_graph_m=(
            math.fsum(record.unknown_graph_m[0] for record in records),
            math.fsum(record.unknown_graph_m[1] for record in records),
            math.fsum(record.unknown_graph_m[2] for record in records),
            math.fsum(record.unknown_graph_m[3] for record in records),
        ),
    )


def total_environment_penalty(cost: EnvironmentCost) -> float:
    """Return the nonnegative increment to the existing route objective."""
    return math.fsum((cost.greenery_m, cost.water_m, cost.traffic_m, cost.wetland_m))

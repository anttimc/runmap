"""Run candidates built from native chunk searches."""

from dataclasses import dataclass, replace
import math
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from runmap.routing.cost_settings import prepared_preference_capabilities, resolve_prepared_preferences
from runmap.routing.settings import effective_run_settings
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.deadlines import check_deadline
from runmap.routing.numeric_search import (LoadedNumericGraph, NumericEndpoint, NumericPath, NumericShapeCosts,
                                           Point, numeric_shortest_path)
from runmap.routing.run_types import RunMode, RunSettings
from runmap.routing.types import EdgeProfile


@dataclass(frozen=True)
class NumericRunCandidate:
    outbound_left: bool | None
    legs: tuple[NumericPath, ...]


@dataclass(frozen=True)
class NumericRunResult:
    candidates: tuple[NumericRunCandidate, ...]
    outcomes: tuple[Literal["complete", "unreachable", "deadline"], ...]
    frontier_node_ids: NDArray[np.int64]


def _used_spans(path: NumericPath) -> tuple[tuple[int, float, float], ...]:
    return tuple((int(segment), min(float(a), float(b)), max(float(a), float(b)))
                 for segment, a, b in zip(path.segment_ids, path.start_m, path.end_m))


def numeric_plan_run(graph: LoadedNumericGraph, home: NumericEndpoint, turnaround: NumericEndpoint,
                     home_point: Point, turnaround_point: Point, settings: RunSettings = RunSettings(), *,
                     profile: EdgeProfile | None = None, preferences: PreferenceSettings = PreferenceSettings(),
                     deadline: float | None = None, mode: RunMode = "round_trip") -> NumericRunResult:
    settings = effective_run_settings(settings, mode)
    if not all(math.isfinite(v) for v in (*home_point, *turnaround_point)) or math.dist(home_point, turnaround_point) < .01:
        raise ValueError("Home and turnaround must be finite and spatially distinct")
    capabilities = prepared_preference_capabilities(graph.metadata.models.hill_model, graph.metadata.hill_valid_fraction)
    resolved = resolve_prepared_preferences(preferences, capabilities)
    candidates: list[NumericRunCandidate] = []
    outcomes: list[Literal["complete", "unreachable", "deadline"]] = []
    frontiers: list[NDArray[np.int64]] = []
    for left in ((True,) if settings.avoidance_ratio == 0 else (True, False)):
        try:
            check_deadline(deadline)
            shape = NumericShapeCosts(home_point, turnaround_point, settings, left)
            outbound = numeric_shortest_path(graph, home, turnaround, profile=profile,
                hill_weight=resolved.weights.hills, shape=shape, deadline=deadline)
            frontiers.append(outbound.frontier_node_ids)
            if outbound.cost is None:
                outcomes.append("unreachable")
                continue
            legs = (outbound,)
            if mode == "round_trip":
                returning = numeric_shortest_path(graph, turnaround, home, profile=profile,
                    hill_weight=resolved.weights.hills,
                    shape=replace(shape, penalize_right=not left, used_spans=_used_spans(outbound)), deadline=deadline)
                frontiers.append(returning.frontier_node_ids)
                if returning.cost is None:
                    outcomes.append("unreachable")
                    continue
                legs += (returning,)
            candidates.append(NumericRunCandidate(left if mode == "round_trip" else None, legs))
            outcomes.append("complete")
        except TimeoutError:
            outcomes.append("deadline")
    frontier = np.unique(np.concatenate(frontiers)) if frontiers else np.empty(0, dtype=np.int64)
    frontier.flags.writeable = False
    return NumericRunResult(tuple(candidates), tuple(outcomes), frontier)

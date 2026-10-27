"""Ranking must preserve constraint precision and diversity before publication."""

import copy
import unittest

from runmap.routing_reference.baseline import _select_alternatives, assess_candidate, rank_candidate
from runmap.routing_reference.loop_types import Assessment, LoopCandidate, LoopRequest
from runmap.routing.types import RouteMetrics


def metrics(distance_m: float = 10000, ascent_m: float = 10) -> RouteMetrics:
    return {
        "distance_m": distance_m, "ascent_m": ascent_m, "descent_m": 10,
        "repeated_distance_m": 0, "repeated_fraction": 0, "stair_distance_m": 0,
        "immediate_reversals": 0, "start_access_reversal": False,
        "start_access_repeated_distance_m": 0, "arc_count": 2, "distinct_segment_count": 2,
    }


def candidate(anchor_id: str, feasible: bool) -> LoopCandidate:
    return {
        "anchor": {"node_id": anchor_id, "shortest_distance_from_start_m": 5000,
                   "bearing_degrees": 0, "longitude": 24, "latitude": 60},
        "generation": {"ascent_cost_per_m": 20, "stairs_multiplier": 8,
                       "return_overlap_factor": 3, "outbound_cost": 5000, "return_cost": 5000},
        "outbound_arc_ids": ["out"], "return_arc_ids": ["back"], "metrics": metrics(),
        "feasible": feasible,
    }


class CandidateRankingTests(unittest.TestCase):
    def test_ranking_is_pure_and_prioritizes_feasibility_before_ascent(self) -> None:
        high_ascent = metrics(ascent_m=100)
        original = copy.deepcopy(high_ascent)
        feasible = rank_candidate(high_ascent, Assessment(0, 0, True), 10000)
        infeasible = rank_candidate(metrics(ascent_m=0), Assessment(1, 0, False), 10000)
        self.assertLess(feasible, infeasible)
        self.assertEqual(high_ascent, original)

    def test_small_violations_remain_infeasible_before_display_rounding(self) -> None:
        request: LoopRequest = {"target_distance_m": 10000, "distance_tolerance_m": 500,
                                "max_repeated_fraction": .1}
        assessment = assess_candidate(metrics(distance_m=10500.0001), request)
        self.assertFalse(assessment.feasible)
        self.assertGreater(assessment.distance_violation_m, 0)
        self.assertEqual(round(assessment.distance_violation_m, 3), 0)

    def test_selection_prefers_distinct_feasible_anchors_then_fills_by_rank(self) -> None:
        first = candidate("a", True)
        same_anchor = candidate("a", True)
        other_anchor = candidate("b", True)
        fallback = candidate("c", False)
        ranked = [fallback, first, same_anchor, other_anchor]
        selected = _select_alternatives(ranked, 4)
        self.assertEqual([id(item) for item in selected],
                         [id(first), id(other_anchor), id(same_anchor), id(fallback)])
        self.assertEqual([id(item) for item in _select_alternatives(ranked, 2)],
                         [id(first), id(other_anchor)])

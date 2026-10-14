"""Real-network adapter invariants independent of a downloaded dataset."""

import math
from time import monotonic
import unittest
from unittest.mock import patch
from collections.abc import Callable

from test_routing import graph_document
from runmap.paths.exploration import Snap
from runmap.routing.fields import integrate_fields
from runmap.routing.run_planner import (RunSettings, endpoint_overlay, plan_run,
                                        projected_points, validate_settings, penalty_strengths)
from runmap.routing.search import GraphData, PathResult, arc_usable, build_graph_index, route_coordinates, shortest_path
from runmap.routing.types import Arc


class RunPlannerTests(unittest.TestCase):
    def test_penalties_ramp_continuously_and_preserve_default(self) -> None:
        self.assertEqual(penalty_strengths(RunSettings(avoidance_ratio=0)), (0, 0, 0))
        tiny = penalty_strengths(RunSettings(avoidance_ratio=1e-6))
        self.assertTrue(all(0 < strength < 1e-8 for strength in tiny))
        one_percent = penalty_strengths(RunSettings(avoidance_ratio=.01))
        self.assertTrue(all(0 < strength < .04 for strength in one_percent))
        default = penalty_strengths(RunSettings())
        self.assertAlmostEqual(default[0], math.exp(.18 ** 2 / (2 * .15 ** 2)))
        self.assertEqual(default[1:], (2, 4))
        previous = (0.0, 0.0, 0.0)
        for ratio in (.001, .01, .05, .1, .179999, .18, .180001, .3):
            strengths = penalty_strengths(RunSettings(avoidance_ratio=ratio))
            self.assertTrue(all(a <= b for a, b in zip(previous, strengths)))
            previous = strengths
        self.assertEqual(penalty_strengths(RunSettings(centre_enabled=False))[0], 0)

    def setUp(self) -> None:
        self.index = build_graph_index(graph_document(
            [("a", 24.8, 60.2), ("b", 24.81, 60.2), ("c", 24.81, 60.21)],
            [("ab", "a", "b", 550, 0, {"highway": "path"}),
             ("bc", "b", "c", 1100, 0, {"highway": "path"})], start="a"))

    def snap(self, segment_id: str, fraction: float) -> Snap:
        segment = self.index.segments[segment_id]
        first, second = segment["geometry"]
        points = projected_points(segment["geometry"])
        return Snap(segment_id, segment["length_m"] * fraction,
                    math.dist(*points) * fraction,
                    first[0] + fraction * (second[0] - first[0]),
                    first[1] + fraction * (second[1] - first[1]), 0)

    def test_two_snaps_on_one_segment_and_unavoidable_retracing(self) -> None:
        result = plan_run(self.index, self.snap("ab", .2), self.snap("ab", .8), RunSettings())
        self.assertEqual(result.outcomes, ("complete", "complete"))
        for candidate in result.candidates:
            self.assertAlmostEqual(candidate.distance_m, 660)
            self.assertAlmostEqual(candidate.repeated_distance_m, 330)
            coordinates = route_coordinates(result.overlay.graph, candidate.outbound.arc_ids)
            self.assertAlmostEqual(coordinates[0][0], 24.802, places=5)
            self.assertAlmostEqual(coordinates[-1][0], 24.808, places=5)
        self.assertEqual(len(self.index.segments), 2)
        self.assertEqual(len(self.index.nodes), 3)

    def test_shared_junction_keeps_both_split_segments(self) -> None:
        overlay = endpoint_overlay(self.index, self.snap("ab", .5), self.snap("bc", .5))
        path = shortest_path(overlay.graph, overlay.home, overlay.turnaround)
        assert path is not None
        self.assertAlmostEqual(path.cost, 825)
        self.assertTrue(all(arc_usable(overlay.graph, overlay.graph.arcs[a]) for a in path.arc_ids))

    def test_pedestrian_oneway_prevents_return(self) -> None:
        self.index.segments["ab"]["tags"]["oneway:foot"] = "yes"
        result = plan_run(self.index, self.snap("ab", .2), self.snap("ab", .8), RunSettings())
        self.assertEqual(result.outcomes, ("unreachable", "unreachable"))

    def test_zero_preferences_recovers_distance_search(self) -> None:
        result = plan_run(self.index, self.snap("ab", .2), self.snap("bc", .5),
                          RunSettings(centre_enabled=False, side_strength=0, reuse_strength=0))
        for candidate in result.candidates:
            self.assertAlmostEqual(candidate.outbound.cost, candidate.distance_m / 2)
            self.assertAlmostEqual(candidate.returning.cost, candidate.distance_m / 2)

    def test_zero_avoidance_disables_all_preferences_and_retraces(self) -> None:
        result = plan_run(self.index, self.snap("ab", .2), self.snap("bc", .5),
                          RunSettings(avoidance_ratio=0))
        self.assertEqual(result.outcomes, ("complete",))
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        graph = result.overlay.graph
        shortest = shortest_path(graph, result.overlay.home, result.overlay.turnaround)
        assert shortest is not None
        self.assertAlmostEqual(candidate.outbound.cost, shortest.cost)
        self.assertAlmostEqual(candidate.returning.cost, shortest.cost)
        self.assertAlmostEqual(candidate.repeated_distance_m, candidate.distance_m / 2)
        self.assertEqual(route_coordinates(graph, candidate.outbound.arc_ids),
                         list(reversed(route_coordinates(graph, candidate.returning.arc_ids))))

    def test_zero_avoidance_still_honours_oneway(self) -> None:
        self.index.segments["ab"]["tags"]["oneway:foot"] = "yes"
        result = plan_run(self.index, self.snap("ab", .2), self.snap("ab", .8),
                          RunSettings(avoidance_ratio=0))
        self.assertEqual(result.outcomes, ("unreachable",))

    def test_invalid_or_coincident_endpoints_and_settings(self) -> None:
        snap = self.snap("ab", .5)
        with self.assertRaises(ValueError):
            endpoint_overlay(self.index, snap, snap)
        for settings in (RunSettings(avoidance_ratio=math.inf), RunSettings(radius_ratio=0),
                         RunSettings(avoidance_ratio=1, radius_ratio=.02)):
            with self.assertRaises(ValueError):
                validate_settings(settings)

    def test_deadline_and_invalid_custom_cost(self) -> None:
        with self.assertRaises(TimeoutError):
            shortest_path(self.index, "a", "c", deadline=monotonic() - 1)
        for value in (-1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                shortest_path(self.index, "a", "c", arc_cost=lambda arc: value)

    def test_deadline_retains_first_completed_candidate(self) -> None:
        calls = 0

        def bounded_search(index: GraphData, start: str, destination: str, *,
                           arc_cost: Callable[[Arc], float], deadline: float | None,
                           heuristic: Callable[[str, str], float]) -> PathResult | None:
            nonlocal calls
            calls += 1
            if calls > 2:
                raise TimeoutError
            return shortest_path(index, start, destination, arc_cost=arc_cost,
                                 deadline=deadline, heuristic=heuristic)

        with patch("runmap.routing.run_planner.shortest_path", side_effect=bounded_search):
            result = plan_run(self.index, self.snap("ab", .2), self.snap("ab", .8), RunSettings())
        self.assertEqual(result.outcomes, ("complete", "deadline"))
        self.assertEqual(len(result.candidates), 1)

    def test_field_cost_survives_geometry_splitting(self) -> None:
        whole = integrate_fields([(0, 0), (100, 20)], (0, 0), (100, 0), 15, 5, .1)
        first = integrate_fields([(0, 0), (33, 6.6)], (0, 0), (100, 0), 15, 5, .1)
        second = integrate_fields([(33, 6.6), (100, 20)], (0, 0), (100, 0), 15, 5, .1)
        for total, a, b in zip(whole, first, second):
            self.assertAlmostEqual(total, a + b, places=4)

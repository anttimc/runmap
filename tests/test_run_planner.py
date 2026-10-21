"""Real-network adapter invariants independent of a downloaded dataset."""

import math
from dataclasses import replace
from time import monotonic
import unittest
from unittest.mock import patch
from collections.abc import Callable

from test_routing import graph_document
from runmap.paths.exploration import Snap
from runmap.routing.cost_preparation import build_request_cost_data, resolve_traversal
from runmap.routing.cost_types import LegCostContext, PreferenceSettings, RequestCostCache
from runmap.routing.costs import arc_cost
from runmap.routing.fields import integrate_fields
from runmap.routing.geometry import indexed_node_point, indexed_segment_points
from runmap.routing.integer_graph import IntegerArc
from runmap.routing.run_planner import (RunSettings, endpoint_overlay, plan_run,
                                        distance_heuristic, projected_points, validate_settings, penalty_strengths)
from runmap.paths.exploration import split_start_graph
from runmap.routing.search import GraphData, PathResult, apply_profile, arc_usable, build_graph_index, route_coordinates, shortest_path


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

    def test_prepared_projection_matches_base_and_overlay_geometry(self) -> None:
        overlay = endpoint_overlay(self.index, self.snap("ab", .2), self.snap("bc", .5))
        for graph in (self.index, overlay.graph):
            for segment_id, segment in graph.segments.items():
                assert graph.integer is not None
                self.assertEqual(graph.projected.segments[segment_id],
                                 tuple(projected_points(segment["geometry"])))
                self.assertIs(indexed_segment_points(graph.projected, graph.integer.segment_indices[segment_id]),
                              graph.projected.segments[segment_id])
            for node_id, node in graph.nodes.items():
                self.assertEqual(graph.projected.nodes[node_id],
                                 projected_points([[node["longitude"], node["latitude"]]])[0])
                self.assertIs(indexed_node_point(graph.projected, graph.integer.node_indices[node_id]),
                              graph.projected.nodes[node_id])
        self.assertIs(overlay.graph.projected.segments["ab"], self.index.projected.segments["ab"])
        self.assertIs(overlay.graph.projected.node_points, self.index.projected.node_points)
        self.assertIs(overlay.graph.projected.segment_points, self.index.projected.segment_points)
        self.assertNotIn(overlay.home, self.index.projected.nodes)

    def test_cached_lower_bound_accounts_for_short_temporary_arcs(self) -> None:
        home = replace(self.snap("ab", .2), graph_offset_m=1)
        overlay = endpoint_overlay(self.index, home, self.snap("bc", .5))
        exploration, _ = split_start_graph(self.index, home)
        for graph in (overlay.graph, exploration):
            self.assertLess(graph.projected.distance_scale, self.index.projected.distance_scale)
            heuristic = distance_heuristic(graph, None)
            for arc in graph.arcs.values():
                self.assertLessEqual(heuristic(arc["from"], arc["to"]), arc["length_m"])
        for graph, start, end in ((overlay.graph, overlay.home, overlay.turnaround),
                                  (exploration, "@snapped-start", "c")):
            astar = shortest_path(graph, start, end, heuristic=distance_heuristic(graph, None))
            self.assertEqual(astar, shortest_path(graph, start, end))

    def test_shape_cost_and_heuristic_reuse_prepared_projection(self) -> None:
        home, turnaround = self.snap("ab", .2), self.snap("bc", .5)
        overlay = endpoint_overlay(self.index, home, turnaround)
        data = build_request_cost_data(overlay.graph, home, turnaround, RunSettings())
        cache = RequestCostCache()
        with patch("runmap.routing.cost_preparation.projected_points", side_effect=AssertionError), \
                patch("runmap.routing.run_planner.projected_points", side_effect=AssertionError):
            heuristic = distance_heuristic(overlay.graph, None)
            result = shortest_path(overlay.graph, overlay.home, overlay.turnaround,
                                   heuristic=heuristic,
                                   arc_cost=lambda arc: arc_cost(arc, data=data, cache=cache,
                                                               leg=LegCostContext(True), deadline=None))
        self.assertIsNotNone(result)
        self.assertGreater(len(cache.shape_fields), 0)

    def test_pedestrian_oneway_prevents_return(self) -> None:
        self.index.segments["ab"]["tags"]["oneway:foot"] = "yes"
        self.index = apply_profile(self.index, self.index.profile)
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
        self.index = apply_profile(self.index, self.index.profile)
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
                           indexed_arc_cost: Callable[[IntegerArc], float], deadline: float | None,
                           indexed_heuristic: Callable[[int, int], float]) -> PathResult | None:
            nonlocal calls
            calls += 1
            if calls > 2:
                raise TimeoutError
            return shortest_path(index, start, destination, indexed_arc_cost=indexed_arc_cost,
                                 deadline=deadline, indexed_heuristic=indexed_heuristic)

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

    def test_shape_integrations_shared_across_candidates_but_not_requests(self) -> None:
        home, turnaround = self.snap("ab", .2), self.snap("bc", .5)
        with patch("runmap.routing.cost_preparation.integrate_fields", wraps=integrate_fields) as integrate:
            result = plan_run(self.index, home, turnaround, RunSettings())
            self.assertEqual(result.outcomes, ("complete", "complete"))
            geometries = [tuple(call.args[0]) for call in integrate.call_args_list]
            self.assertGreater(len(geometries), 0)
            self.assertEqual(len(geometries), len(set(geometries)))
            first_count = integrate.call_count
            plan_run(self.index, home, turnaround, RunSettings())
            self.assertEqual(integrate.call_count, 2 * first_count)

    def test_leg_contexts_share_features_without_leaking_side_or_reuse(self) -> None:
        overlay = endpoint_overlay(self.index, self.snap("ab", .2), self.snap("ab", .8))
        data = build_request_cost_data(overlay.graph, self.snap("ab", .2),
                                       self.snap("ab", .8), RunSettings())
        cache = RequestCostCache()
        arc = overlay.graph.arcs[overlay.graph.outgoing[overlay.home][0]]
        unused = LegCostContext(True)
        baseline = arc_cost(arc, data=data, cache=cache, leg=unused, deadline=None)
        segment = data.index.segment_indices[arc["segment_id"]]
        returning = LegCostContext(False, frozenset({segment}))
        reused = arc_cost(arc, data=data, cache=cache, leg=returning, deadline=None)
        self.assertGreater(reused, baseline)
        self.assertEqual(arc_cost(arc, data=data, cache=cache, leg=unused, deadline=None), baseline)
        # A different piece of the same original physical segment is not reuse.
        other_part = next(part for part in overlay.parts if part != segment)
        disjoint = LegCostContext(True, frozenset({other_part}))
        self.assertEqual(arc_cost(arc, data=data, cache=cache, leg=disjoint, deadline=None), baseline)
        with self.assertRaises(TimeoutError):
            arc_cost(arc, data=data, cache=cache, leg=unused, deadline=monotonic() - 1)

    def test_zero_width_skips_shape_preparation(self) -> None:
        with patch("runmap.routing.cost_preparation.integrate_fields",
                   side_effect=AssertionError("Zero-width run must not integrate shape fields")):
            result = plan_run(self.index, self.snap("ab", .2), self.snap("bc", .5),
                              RunSettings(avoidance_ratio=0))
        self.assertEqual(result.outcomes, ("complete",))

    def test_selected_leg_breakdowns_retain_the_original_reuse_context(self) -> None:
        for width in (0, .18):
            result = plan_run(self.index, self.snap("ab", .2), self.snap("bc", .5),
                              RunSettings(avoidance_ratio=width))
            for candidate in result.candidates:
                self.assertEqual(candidate.outbound_costs.reuse, 0)
                self.assertEqual(candidate.returning_costs.reuse > 0, width > 0)
                for path, costs in ((candidate.outbound, candidate.outbound_costs),
                                    (candidate.returning, candidate.returning_costs)):
                    self.assertAlmostEqual(path.cost, costs.total)
                    self.assertGreaterEqual(costs.total, costs.distance_m)
                    self.assertEqual((costs.hills, costs.signals, costs.busy_roads, costs.non_green), (0, 0, 0, 0))

    def test_split_references_preserve_physical_spans_and_reverse_direction(self) -> None:
        home, turnaround = self.snap("ab", .2), self.snap("ab", .8)
        overlay = endpoint_overlay(self.index, home, turnaround)
        data = build_request_cost_data(overlay.graph, home, turnaround, RunSettings(), parts=overlay.parts)
        path = shortest_path(overlay.graph, overlay.home, overlay.turnaround)
        assert path is not None
        arc = overlay.graph.arcs[path.arc_ids[0]]
        forward = resolve_traversal(data, arc)
        reverse = resolve_traversal(data, overlay.graph.arcs[arc["id"][:-1] + "b"])
        self.assertEqual(forward.segment_id, "ab")
        self.assertAlmostEqual(forward.graph_start_m, 110)
        self.assertAlmostEqual(forward.graph_end_m, 440)
        self.assertFalse(forward.reverse)
        self.assertTrue(reverse.reverse)
        self.assertEqual((forward.graph_start_m, forward.graph_end_m), (reverse.graph_start_m, reverse.graph_end_m))
        unsplit = resolve_traversal(data, overlay.graph.arcs["bc:b"])
        self.assertEqual((unsplit.segment_id, unsplit.graph_start_m, unsplit.graph_end_m, unsplit.reverse),
                         ("bc", 0, 1100, True))

    def test_unsupported_preferences_rejected_before_overlay_or_search(self) -> None:
        with patch("runmap.routing.run_planner.endpoint_overlay", side_effect=AssertionError):
            with self.assertRaisesRegex(ValueError, "Unsupported hills"):
                plan_run(self.index, self.snap("ab", .2), self.snap("bc", .5), RunSettings(),
                          preferences=PreferenceSettings(hills="low"))

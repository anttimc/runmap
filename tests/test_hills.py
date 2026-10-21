"""Directional terrain costs, partial traversal conservation, and uncertainty."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, replace
from functools import partial
import math
import random
from time import monotonic
import unittest
from unittest.mock import patch

import numpy as np

from test_routing import graph_document
from runmap.paths.exploration import Snap
from runmap.paths.measurements import build_measurement_data, graph_digest
from runmap.routing.cost_preparation import build_request_cost_data
from runmap.routing.cost_settings import resolve_preferences
from runmap.routing.cost_types import LegCostContext, PreferenceSettings, RequestCostCache, TraversalRef
from runmap.routing.costs import arc_cost, indexed_arc_cost
from runmap.routing.geometry import projected_points
from runmap.routing.hill_types import HillData
from runmap.routing.hills import (build_hill_data, grade_response, hill_observations,
                                  hill_penalty_response, running_energy_cost, running_energy_response,
                                  sum_hill_features, traversal_hills, indexed_traversal_hills,
                                  _prepare_segment, _scaled)
from runmap.routing.run_planner import RunSettings, distance_heuristic, endpoint_overlay, plan_run
from runmap.routing.run_planner import indexed_distance_heuristic
from runmap.routing.integer_graph import IntegerGraph
from runmap.routing.search import apply_profile, build_graph_index, shortest_path
from runmap.routing.types import GraphDocument, Measurement, MeasurementDocument


def hill_document(graph: GraphDocument, grades: Mapping[str, Sequence[float | None]], *,
                  flags: tuple[str, ...] = (), projected_scale: float = 1) -> MeasurementDocument:
    measurements: dict[str, list[Measurement]] = {}
    for segment in graph["segments"]:
        values = grades[segment["id"]]
        items: list[Measurement] = []
        for index, grade in enumerate(values):
            span = segment["length_m"] / len(values)
            start, end = index * span, (index + 1) * span
            items.append({"segment_id": segment["id"], "interval_index": index,
                          "graph_start_m": start, "graph_end_m": end,
                          "start_m": start * projected_scale, "end_m": end * projected_scale,
                          "grade_percent": grade, "height_change_m": grade * span * projected_scale / 100 if grade is not None else None,
                          "valid": grade is not None, "quality_flags": list(flags), "geometry": segment["geometry"]})
        measurements[segment["id"]] = items
    return {"schema_version": 1, "graph_digest": graph_digest(graph), "segments": measurements,
            "method": {"schema_version": 1, "manifest_id": "fixture", "source": "synthetic plane",
                       "wavelength_m": 40, "sampling_interval_m": 10, "method": "fixture",
                       "chainage_crs": "EPSG:3067", "graph_chainage": "fixture",
                       "profile_kind": "fixture", "valid_fraction": 1}}


class HillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = graph_document(
            [("a", 24.8, 60.2), ("b", 24.801, 60.2), ("c", 24.8005, 60.201)],
            [("ab", "a", "b", 100, 0, {"highway": "path"}),
             ("ac", "a", "c", 70, 0, {"highway": "path"}),
             ("cb", "c", "b", 70, 0, {"highway": "path"})], start="a")
        self.index = build_graph_index(self.graph)
        self.home = Snap("ab", 0, 0, 24.8, 60.2, 0)
        self.turnaround = Snap("ab", 100, math.dist(*projected_points(self.graph["segments"][0]["geometry"])),
                               24.801, 60.2, 0)

    def hills(self, grades: Sequence[float | None], *, flags: tuple[str, ...] = (),
              projected_scale: float = 1) -> HillData:
        return build_hill_data(self.index, hill_document(self.graph, {"ab": grades, "ac": [0], "cb": [0]},
                                                         flags=flags, projected_scale=projected_scale))

    def test_packed_measurements_match_document_hills_and_require_shared_graph(self) -> None:
        document = hill_document(self.graph, {"ab": [5, None, -10], "ac": [], "cb": [0]}, flags=("bridge",))
        packed = build_measurement_data(self.graph, document, index=self.index.integer)
        original = build_hill_data(self.index, document)
        actual = build_hill_data(self.index, packed)
        for name in ("interval_offsets", "chainage", "features", "totals"):
            np.testing.assert_array_equal(getattr(original, name), getattr(actual, name))
        with self.assertRaisesRegex(ValueError, "share the graph"):
            build_hill_data(build_graph_index(deepcopy(self.graph)), packed)


    def test_hill_arrays_share_segment_order_and_are_read_only(self) -> None:
        self.graph["segments"].reverse()
        hills = self.hills([5, -10])
        self.assertIs(hills.index, self.index.integer)
        self.assertEqual(hills.index.segment_ids, ("ab", "ac", "cb"))
        np.testing.assert_array_equal(hills.interval_offsets, [0, 2, 3, 4])
        self.assertEqual(hills.features.shape, (4, 2, 7))
        self.assertEqual(hills.totals.shape, (3, 2, 7))
        self.assertEqual(indexed_traversal_hills(hills, 0, 0, 100, False).ascent_m, 2.5)
        for array in (hills.interval_offsets, hills.chainage, hills.features, hills.totals):
            with self.assertRaises(ValueError):
                array.flat[0] = 0

    def test_full_segments_use_precomputed_totals(self) -> None:
        hills = self.hills([5, -10])
        with patch("runmap.routing.hills.np.searchsorted", side_effect=AssertionError("interval search")), \
                patch("runmap.routing.hills._scaled", side_effect=AssertionError("interval scaling")):
            forward = indexed_traversal_hills(hills, 0, 0, 100, False)
            backward = indexed_traversal_hills(hills, 0, 0, 100, True)
        self.assertEqual((forward.ascent_m, backward.descent_m), (2.5, 2.5))

    def test_indexed_partial_spans_match_original_interval_summation(self) -> None:
        document = hill_document(self.graph, {"ab": [1.7, None, -12.1, 22], "ac": [0], "cb": [5, -5, 10]},
                                 projected_scale=.8)
        document["segments"]["ab"][2]["quality_flags"] = ["bridge"]
        hills = build_hill_data(self.index, document)
        rng = random.Random(42)
        for segment, segment_id in enumerate(hills.index.segment_ids):
            source = self.index.segments[segment_id]
            original = _prepare_segment(source, document["segments"][segment_id], hills.model)
            spans = [(0.0, source["length_m"])] + [tuple(sorted((
                rng.uniform(0, source["length_m"]), rng.uniform(0, source["length_m"])))) for _ in range(50)]
            for start, end in spans:
                for reverse in (False, True):
                    expected = sum_hill_features(
                        _scaled(item.backward if reverse else item.forward,
                                (min(end, item.graph_end_m) - max(start, item.graph_start_m))
                                / (item.graph_end_m - item.graph_start_m))
                        for item in original if min(end, item.graph_end_m) > max(start, item.graph_start_m))
                    actual = indexed_traversal_hills(hills, segment, start, end, reverse)
                    self.assertEqual(asdict(actual), asdict(expected))

    def test_hill_ordering_must_match_the_request_graph(self) -> None:
        hills = self.hills([5])
        assert self.index.integer is not None
        different_order = replace(self.index.integer, segment_ids=tuple(reversed(self.index.integer.segment_ids)))
        graph = replace(self.index, integer=different_order)
        with self.assertRaisesRegex(ValueError, "integer ordering"):
            build_request_cost_data(graph, self.home, self.turnaround, RunSettings(),
                                    preferences=PreferenceSettings(hills="low"), hills=hills)
        policy = apply_profile(self.index, self.index.profile)
        data = build_request_cost_data(policy, self.home, self.turnaround, RunSettings(),
                                       preferences=PreferenceSettings(hills="low"), hills=hills)
        self.assertIs(data.hills, hills)

    def test_indexed_lookup_rejects_invalid_ranges_and_honors_deadlines(self) -> None:
        hills = self.hills([5])
        for segment, start, end in ((-1, 0, 100), (3, 0, 100), (0, -1, 100),
                                    (0, 20, 20), (0, 0, 101), (0, math.nan, 100)):
            with self.assertRaises(ValueError):
                indexed_traversal_hills(hills, segment, start, end, False)
        with self.assertRaises(TimeoutError):
            indexed_traversal_hills(hills, 0, 0, 100, False, monotonic() - 1)

    def test_search_and_costs_do_not_read_json_records_or_string_geometry(self) -> None:
        hills = self.hills([5, -10])
        overlay = endpoint_overlay(self.index, self.home, self.turnaround)
        assert overlay.graph.integer is not None

        def without_records(index: IntegerGraph) -> IntegerGraph:
            records = {arc.index: replace(arc, record={})
                       for arc in (*index.arcs, *index.extra_arcs, *index.arc_overrides.values())}
            return replace(index, arcs=tuple(records[a.index] for a in index.arcs),
                           extra_arcs=tuple(records[a.index] for a in index.extra_arcs),
                           arc_overrides={key: records[a.index] for key, a in index.arc_overrides.items()},
                           outgoing=tuple(tuple(records[a.index] for a in row) for row in index.outgoing),
                           incoming=tuple(tuple(records[a.index] for a in row) for row in index.incoming),
                           outgoing_overrides={key: tuple(records[a.index] for a in row)
                                               for key, row in index.outgoing_overrides.items()})

        graph = replace(overlay.graph, arcs={}, segments={}, integer=without_records(overlay.graph.integer),
                        projected=overlay.graph.projected)
        for width in (0.0, .18):
            for level in ("off", "low", "high"):
                settings = RunSettings(avoidance_ratio=width)
                preference = PreferenceSettings(hills=level)
                results = []
                for current in (overlay.graph, graph):
                    data = build_request_cost_data(current, self.home, self.turnaround, settings,
                                                   parts=overlay.parts, preferences=preference, hills=hills)
                    cache = RequestCostCache()
                    result = shortest_path(current, overlay.home, overlay.turnaround,
                                           indexed_arc_cost=partial(indexed_arc_cost, data=data, cache=cache,
                                                                    leg=LegCostContext(True), deadline=None),
                                           indexed_heuristic=indexed_distance_heuristic(current, None))
                    results.append(result)
                    self.assertTrue(all(isinstance(key, int) for key in cache.shape_fields))
                    self.assertTrue(all(isinstance(key, int) for key in cache.hill_features))
                self.assertEqual(results[0], results[1])

    def test_response_uses_published_cr_coefficients_and_fractional_grade(self) -> None:
        for grade, expected in ((0, 3.6), (10, 5.968214), (-10, 2.151706),
                                (20, 9.006688), (-20, 1.800032)):
            with self.subTest(grade=grade):
                self.assertAlmostEqual(running_energy_cost(grade), expected)
                self.assertAlmostEqual(running_energy_response(grade), expected / 3.6 * math.hypot(1, grade / 100))
        self.assertEqual(grade_response(0), 1)
        self.assertLess(grade_response(-10), grade_response(0))
        self.assertLess(grade_response(-20), grade_response(-10))
        self.assertGreater(grade_response(-40), grade_response(-20))

    def test_uphill_preference_reduces_moderate_and_increases_steep_slopes(self) -> None:
        for grade in (1, 2, 3, 4):
            self.assertLess(grade_response(grade), running_energy_response(grade))
        self.assertEqual(grade_response(5), running_energy_response(5))
        for grade in (6, 10, 15, 20, 45, 60):
            self.assertGreater(grade_response(grade), running_energy_response(grade))
        for grade in (-60, -20, -5, 0):
            self.assertEqual(grade_response(grade), running_energy_response(grade))
        for weight in (1, 6):
            steep = 100 * (1 + weight * hill_penalty_response(10))
            gentle = 200 * (1 + weight * hill_penalty_response(5))
            self.assertLess(gentle, steep)  # Both gain 10 m; gentle is twice as long.

    def test_hill_penalty_excludes_flat_baseline_and_downhill_rewards(self) -> None:
        for grade in (-20, -10, -5, 0):
            self.assertEqual(hill_penalty_response(grade), 0)
        for grade in (1, 3, 5, 10, 20, 45):
            self.assertAlmostEqual(hill_penalty_response(grade), grade_response(grade) - 1)
        self.assertGreater(hill_penalty_response(-45), 0)
        hills = self.hills([0])
        for level in ("off", "low", "high"):
            result = plan_run(self.index, self.home, self.turnaround, RunSettings(),
                              preferences=PreferenceSettings(hills=level), hills=hills)
            for candidate in result.candidates:
                self.assertEqual(candidate.outbound_costs.hills + candidate.returning_costs.hills, 0)
                self.assertEqual(candidate.distance_m, 240)

    def test_response_has_no_cap_and_tails_are_continuous_positive_and_increasing(self) -> None:
        for sign in (-1, 1):
            costs = [running_energy_cost(sign * grade) for grade in (20, 30, 45, 60, 100, 1000)]
            self.assertEqual(costs, sorted(costs))
            self.assertTrue(all(cost > 0 for cost in costs))
            boundary = running_energy_cost(sign * 45)
            self.assertAlmostEqual(running_energy_cost(sign * (45 + 1e-7)), boundary, places=6)
            self.assertAlmostEqual(running_energy_cost(sign * (45 - 1e-7)), boundary, places=6)
            self.assertAlmostEqual(running_energy_cost(sign * 60) - boundary,
                                   running_energy_cost(sign * 75) - running_energy_cost(sign * 60))
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                grade_response(value)

    def test_partial_spans_add_and_reverse_ascent_descent(self) -> None:
        hills = self.hills([5, -10], projected_scale=.8)
        whole = traversal_hills(hills, TraversalRef("ab", 0, 100, False))
        split = sum_hill_features(traversal_hills(hills, TraversalRef("ab", a, b, False))
                                  for a, b in ((0, 25), (25, 75), (75, 100)))
        self.assertAlmostEqual(whole.penalty_m, split.penalty_m)
        self.assertAlmostEqual(whole.length_m, 80)
        self.assertEqual((whole.ascent_m, whole.descent_m), (2, 4))
        reverse = traversal_hills(hills, TraversalRef("ab", 0, 100, True))
        self.assertEqual((reverse.ascent_m, reverse.descent_m), (4, 2))
        self.assertAlmostEqual(reverse.penalty_m, 40 * hill_penalty_response(-5) + 40 * hill_penalty_response(10))
        first = traversal_hills(hills, TraversalRef("ab", 0, 25, False))
        self.assertEqual(first.length_m, 20)
        self.assertAlmostEqual(first.penalty_m, 20 * hill_penalty_response(5))

    def test_missing_bridge_tunnel_intervals_get_fallback_without_fake_flat_observations(self) -> None:
        for grades, flags in (([None], ()), ([0], ("bridge",)), ([0], ("tunnel",))):
            with self.subTest(grades=grades, flags=flags):
                hills = self.hills(grades, flags=flags)
                features = traversal_hills(hills, TraversalRef("ab", 0, 100, False))
                self.assertAlmostEqual(features.penalty_m, 100 * (running_energy_response(20) - 1))
                self.assertEqual(features.fallback_length_m, 100)
                observation = hill_observations(features)
                self.assertEqual(observation.terrain_trusted_fraction, 0)
                self.assertEqual(observation.terrain_valid_fraction, 0 if grades == [None] else 1)
                if grades == [None]:
                    self.assertIsNone(observation.estimated_ascent_m)

    def test_missing_entire_profile_falls_back_and_chainage_gaps_are_rejected(self) -> None:
        hills = self.hills([])
        features = traversal_hills(hills, TraversalRef("ab", 0, 100, False))
        fallback = features.fallback_length_m
        self.assertAlmostEqual(fallback, math.dist(*projected_points(self.graph["segments"][0]["geometry"])))
        self.assertAlmostEqual(features.penalty_m, fallback * (running_energy_response(20) - 1))
        document = hill_document(self.graph, {"ab": [5, 5], "ac": [0], "cb": [0]})
        document["segments"]["ab"][1]["graph_start_m"] += 1
        with self.assertRaisesRegex(ValueError, "contiguous"):
            build_hill_data(self.index, document)

    def test_zero_width_hills_search_return_independently_and_use_both_directions(self) -> None:
        hills = self.hills([10])
        with patch("runmap.routing.run_planner.reverse_path", side_effect=AssertionError("Cannot retrace asymmetric costs")):
            result = plan_run(self.index, self.home, self.turnaround, RunSettings(avoidance_ratio=0),
                              preferences=PreferenceSettings(hills="high"), hills=hills)
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.outbound.arc_ids, ("ac:f", "cb:f"))
        self.assertEqual(candidate.returning_traversals[0].segment_id, "ab")
        self.assertTrue(candidate.returning_traversals[0].reverse)
        self.assertEqual(candidate.distance_m, 240)
        self.assertEqual(candidate.returning_costs.hills, 0)
        self.assertEqual(candidate.outbound_costs.hills, 0)
        self.assertEqual(candidate.returning_observations.estimated_descent_m, 10)
        self.assertAlmostEqual(candidate.returning.cost, candidate.returning_costs.total)
        self.assertEqual((candidate.returning_costs.centre, candidate.returning_costs.side, candidate.returning_costs.reuse), (0, 0, 0))
        off = plan_run(self.index, self.home, self.turnaround, RunSettings(avoidance_ratio=0))
        self.assertGreater(candidate.distance_m, off.candidates[0].distance_m)

    def test_longer_gentle_climb_is_selected_over_short_steep_climb(self) -> None:
        graph = deepcopy(self.graph)
        for segment in graph["segments"]:
            segment["length_m"] = 100
        for arc in graph["arcs"]:
            arc["length_m"] = 100
        index = build_graph_index(graph)
        hills = build_hill_data(index, hill_document(graph, {"ab": [10], "ac": [5], "cb": [5]}))
        for level in ("low", "high"):
            with self.subTest(level=level):
                result = plan_run(index, self.home, self.turnaround, RunSettings(avoidance_ratio=0),
                                  preferences=PreferenceSettings(hills=level), hills=hills)
                candidate = result.candidates[0]
                self.assertEqual(candidate.outbound.arc_ids, ("ac:f", "cb:f"))
                self.assertEqual(candidate.outbound_observations.estimated_ascent_m, 10)
                self.assertEqual(sum(index.arcs[arc]["length_m"] for arc in candidate.outbound.arc_ids), 200)
                self.assertEqual(candidate.returning_traversals[0].segment_id, "ab")
                self.assertTrue(candidate.returning_traversals[0].reverse)

    def test_high_accepts_longer_mild_climb_without_flat_energy_charge(self) -> None:
        hills = build_hill_data(self.index, hill_document(
            self.graph, {"ab": [5], "ac": [25 / 7], "cb": [25 / 7]}))
        result = plan_run(self.index, self.home, self.turnaround, RunSettings(avoidance_ratio=0),
                          preferences=PreferenceSettings(hills="high"), hills=hills)
        candidate = result.candidates[0]
        self.assertEqual(candidate.outbound.arc_ids, ("ac:f", "cb:f"))
        self.assertAlmostEqual(candidate.outbound_observations.estimated_ascent_m, 5)
        self.assertEqual(sum(result.overlay.graph.arcs[a]["length_m"]
                             for a in candidate.outbound.arc_ids), 140)
        # Both alternatives gain 5 m; the previous full-energy term chose 100 m at 5%.
        self.assertLess(100 * (1 + 6 * grade_response(5)),
                        140 * (1 + 6 * grade_response(25 / 7)))

    def test_both_loop_assignments_optimize_each_leg_with_signed_costs(self) -> None:
        hills = self.hills([10])
        settings = RunSettings()
        with patch("runmap.routing.run_planner.shortest_path", wraps=shortest_path) as search:
            result = plan_run(self.index, self.home, self.turnaround, settings,
                              preferences=PreferenceSettings(hills="high"), hills=hills)
        self.assertEqual(result.outcomes, ("complete", "complete"))
        self.assertEqual(search.call_count, 4)
        contexts = [call.kwargs["indexed_arc_cost"].keywords["leg"] for call in search.call_args_list]
        self.assertEqual([context.penalize_right for context in contexts], [True, False, False, True])
        graph = result.overlay.graph
        data = build_request_cost_data(graph, self.home, self.turnaround, settings,
                                      parts=result.overlay.parts, preferences=PreferenceSettings(hills="high"), hills=hills)
        for candidate in result.candidates:
            assert graph.integer is not None
            used = frozenset(graph.integer.segment_indices[graph.arcs[arc]["segment_id"]]
                             for arc in candidate.outbound.arc_ids)
            legs = ((candidate.outbound, result.overlay.home, result.overlay.turnaround,
                     LegCostContext(candidate.outbound_left)),
                    (candidate.returning, result.overlay.turnaround, result.overlay.home,
                     LegCostContext(not candidate.outbound_left, used)))
            for path, start, end, context in legs:
                oracle = shortest_path(graph, start, end, arc_cost=partial(
                    arc_cost, data=data, cache=RequestCostCache(), leg=context, deadline=None))
                assert oracle is not None
                self.assertEqual(path.arc_ids, oracle.arc_ids)
                self.assertAlmostEqual(path.cost, oracle.cost)

    def test_hill_astar_matches_dijkstra_and_deadline_covers_feature_work(self) -> None:
        hills = self.hills([10])
        overlay = endpoint_overlay(self.index, self.home, self.turnaround)
        data = build_request_cost_data(overlay.graph, self.home, self.turnaround, RunSettings(avoidance_ratio=0),
                                       parts=overlay.parts, preferences=PreferenceSettings(hills="high"), hills=hills)
        cache = RequestCostCache()
        cost = partial(arc_cost, data=data, cache=cache, leg=LegCostContext(True), deadline=None)
        astar = shortest_path(overlay.graph, overlay.home, overlay.turnaround, arc_cost=cost,
                              heuristic=distance_heuristic(overlay.graph, None))
        dijkstra = shortest_path(overlay.graph, overlay.home, overlay.turnaround, arc_cost=cost)
        assert astar is not None and dijkstra is not None
        self.assertEqual(astar.arc_ids, dijkstra.arc_ids)
        self.assertEqual(astar.cost, dijkstra.cost)
        with self.assertRaises(TimeoutError):
            traversal_hills(hills, TraversalRef("ab", 0, 100, False), monotonic() - 1)

    def test_off_skips_hill_features_and_unavoidable_hill_still_returns_a_route(self) -> None:
        hills = self.hills([10])
        with patch("runmap.routing.cost_preparation.indexed_traversal_hills", side_effect=AssertionError):
            result = plan_run(self.index, self.home, self.turnaround, RunSettings(avoidance_ratio=0), hills=hills)
        self.assertEqual(result.candidates[0].distance_m, 200)
        for segment_id in ("ac", "cb"):
            self.index.segments[segment_id]["tags"]["foot"] = "no"
        self.index = apply_profile(self.index, self.index.profile)
        result = plan_run(self.index, self.home, self.turnaround, RunSettings(avoidance_ratio=0),
                          preferences=PreferenceSettings(hills="high"), hills=hills)
        self.assertEqual(result.outcomes, ("complete",))
        self.assertEqual(result.candidates[0].distance_m, 200)

    def test_feature_identity_and_level_weights(self) -> None:
        hills = self.hills([5])
        self.assertEqual([resolve_preferences(PreferenceSettings(hills=level), hills=hills).weights.hills
                          for level in ("off", "low", "high")], [0, 1, 6])
        other = build_graph_index(deepcopy(self.graph))
        with self.assertRaisesRegex(ValueError, "different prepared graph"):
            plan_run(other, self.home, self.turnaround, RunSettings(),
                      preferences=PreferenceSettings(hills="low"), hills=hills)

    def test_directed_hill_cache_is_shared_within_a_request_and_isolated_between_requests(self) -> None:
        hills = self.hills([5])
        with patch("runmap.routing.cost_preparation.indexed_traversal_hills", wraps=indexed_traversal_hills) as integrate:
            result = plan_run(self.index, self.home, self.turnaround, RunSettings(),
                              preferences=PreferenceSettings(hills="low"), hills=hills)
            self.assertEqual(result.outcomes, ("complete", "complete"))
            references = [call.args[1:5] for call in integrate.call_args_list]
            self.assertGreater(len(references), 0)
            self.assertEqual(len(references), len(set(references)))
            first_count = integrate.call_count
            plan_run(self.index, self.home, self.turnaround, RunSettings(),
                      preferences=PreferenceSettings(hills="low"), hills=hills)
            self.assertEqual(integrate.call_count, 2 * first_count)

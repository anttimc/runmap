"""Preference contracts and lower bounds independent of real OSM data."""

from dataclasses import replace
import math
from typing import cast
import unittest

from test_routing import graph_document
from runmap.routing.cost_settings import resolve_preferences, validate_weight_table
from runmap.routing_reference.cost_types import LegCostContext, ShapeFeatures
from runmap.routing.cost_types import PreferenceCosts, PreferenceLevel, PreferenceSettings, PreferenceWeights, PreferenceWeightTable, ShapeStrengths
from runmap.routing_reference.costs import evaluate_arc_breakdown, evaluate_arc_cost
from runmap.routing_reference.run_planner import distance_heuristic
from runmap.routing.graph import build_graph_index
from runmap.routing_reference.search import shortest_path
from runmap.routing.types import Arc


class CostContractTests(unittest.TestCase):
    def test_all_off_resolves_without_prepared_preference_features(self) -> None:
        resolved = resolve_preferences(PreferenceSettings())
        self.assertEqual(resolved.weights, PreferenceWeights())
        self.assertEqual(resolved.weight_tables.hills.version, "minetti-excess-equivalent-metres-v4")

    def test_each_unimplemented_level_rejected_explicitly(self) -> None:
        for name in ("hills", "signals", "busy_roads", "greenery"):
            for level in ("low", "high"):
                with self.subTest(preference=name, level=level):
                    with self.assertRaisesRegex(ValueError, f"Unsupported {name}"):
                        resolve_preferences(replace(PreferenceSettings(), **{name: level}))
        with self.assertRaisesRegex(ValueError, "Invalid hills"):
            resolve_preferences(PreferenceSettings(hills=cast(PreferenceLevel, "maximum")))

    def test_invalid_weight_tables_cannot_silently_disable_preferences(self) -> None:
        for value in (-1, math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite and nonnegative"):
                    validate_weight_table(PreferenceWeightTable("test-v1", low=value))
        with self.assertRaisesRegex(ValueError, "Off preference weight"):
            validate_weight_table(PreferenceWeightTable("test-v1", off=1))
        with self.assertRaisesRegex(ValueError, "needs a version"):
            validate_weight_table(PreferenceWeightTable(""))

    def test_scalar_and_breakdown_share_components_and_length_lower_bound(self) -> None:
        features = ShapeFeatures(15, 10, 40)
        strengths = ShapeStrengths(2, 3, 4)
        preferences = PreferenceCosts(20, 30, 40, 50)
        for side in (True, False):
            for reused in (True, False):
                leg = LegCostContext(side)
                scalar = evaluate_arc_cost(100, features, strengths, leg, reused, preferences)
                costs = evaluate_arc_breakdown(100, features, strengths, leg, reused, preferences)
                self.assertEqual(scalar, costs.total)
                self.assertEqual(costs.total, sum((costs.distance_m, costs.centre, costs.side,
                                                  costs.reuse, costs.hills, costs.signals,
                                                  costs.busy_roads, costs.non_green)))
                self.assertGreaterEqual(scalar, 100)
        for value in (-1, math.inf, math.nan):
            with self.assertRaisesRegex(ValueError, "finite and nonnegative"):
                evaluate_arc_cost(100, features, strengths, LegCostContext(True), False,
                                   PreferenceCosts(hills=value))
        with self.assertRaisesRegex(ValueError, "Total cost must be finite"):
            evaluate_arc_cost(1e308, ShapeFeatures(1e308, 0, 0), ShapeStrengths(1, 0, 0),
                               LegCostContext(True), False)

    def test_preference_cost_astar_matches_dijkstra_on_alternative_routes(self) -> None:
        graph = build_graph_index(graph_document(
            [("s", 24.8, 60.2), ("a", 24.801, 60.2), ("b", 24.801, 60.201), ("t", 24.802, 60.2)],
            [("sa", "s", "a", 100, 0, {"highway": "path"}),
             ("at", "a", "t", 100, 0, {"highway": "path"}),
             ("sb", "s", "b", 160, 0, {"highway": "path"}),
             ("bt", "b", "t", 160, 0, {"highway": "path"})], start="s"))

        def cost(arc: Arc) -> float:
            penalty = PreferenceCosts(hills=300) if arc["segment_id"] == "at" else PreferenceCosts()
            return evaluate_arc_cost(arc["length_m"], ShapeFeatures(0, 0, 0),
                                      ShapeStrengths(0, 0, 0), LegCostContext(True), False, penalty)

        astar = shortest_path(graph, "s", "t", arc_cost=cost, heuristic=distance_heuristic(graph, None))
        dijkstra = shortest_path(graph, "s", "t", arc_cost=cost)
        assert astar is not None and dijkstra is not None
        self.assertEqual(astar.arc_ids, ("sb:f", "bt:f"))
        self.assertEqual(astar.arc_ids, dijkstra.arc_ids)
        self.assertEqual(astar.cost, dijkstra.cost)

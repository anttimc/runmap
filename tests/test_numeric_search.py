"""Native search agrees with the Python reference without per-edge callbacks."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import math
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
from unittest.mock import patch
from typing import cast

import numpy as np
from rasterio.warp import transform

from runmap import _routing_native
from runmap.paths.database import export_network_database, open_network_database
from runmap.paths.environment_join import EnvironmentInterval
from runmap.paths.exploration import Snap
from runmap.routing_reference.exploration import split_start_graph
from runmap.paths.local_network import load_local_network
from runmap.paths.graph import write_graph_outputs
from runmap.paths.numeric_database import export_numeric_database, iter_numeric_chunks, open_numeric_database
from runmap.paths.numeric_format import decode_numeric_chunk, encode_numeric_chunk
from runmap.paths.numeric_hills import read_hill_intervals
from runmap.paths.numeric_preparation import prepare_numeric_chunk
from runmap.paths.graph_chunks import TopologyChunk
from runmap.routing_reference.cost_preparation import build_request_cost_data
from runmap.routing_reference.cost_types import LegCostContext, RequestCostCache
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.environment_costs import EnvironmentWeights, evaluate_environment_cost
from runmap.routing_reference.costs import indexed_arc_cost, path_cost_breakdown
from runmap.routing.geometry import projected_points
from runmap.routing.numeric_search import (NumericEndpoint, NumericShapeCosts,
                                          load_numeric_graph, numeric_distances, numeric_shortest_path)
from runmap.routing.profiles import exploration_profile
from runmap.routing_reference.run_planner import endpoint_overlay, plan_run
from runmap.routing.numeric_planner import numeric_plan_run
from runmap.routing.run_types import RunSettings
from runmap.routing_reference.search import shortest_distances, shortest_path
from test_database_features import BOUNDS
from test_path_database import publish_fixture
from test_routing import graph_document
from test_measurements import enrich


class NumericSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, _, _ = publish_fixture(self.root)
        self.reference, self.numeric = self.root / "network.sqlite", self.root / "numeric.sqlite"
        with patch("runmap.paths.graph_chunks.MAX_CHUNK_SEGMENTS", 1):
            export_network_database(self.source, self.reference)
        export_numeric_database(self.reference, self.numeric)
        with closing(open_numeric_database(self.numeric)) as connection:
            self.graph = load_numeric_graph(connection, BOUNDS)
            self.nodes = {key: number for number, key in connection.execute(
                "SELECT number, external_id FROM identities WHERE kind='node'")}
            self.segments = {key: number for number, key in connection.execute(
                "SELECT number, external_id FROM identities WHERE kind='segment'")}
            self.hill_intervals = {key: read_hill_intervals(connection, number) for key, number in self.segments.items()}
        self.keys = {number: key for key, number in self.nodes.items()}

    def _endpoint(self, key: str) -> NumericEndpoint:
        return NumericEndpoint(node_id=self.nodes[key])

    def test_native_environment_partial_cost_matches_pure_function(self) -> None:
        selected = set(np.frombuffer(self.graph.native.selected_segment_ids(), dtype="<i8").tolist())
        with closing(open_network_database(self.reference)) as connection:
            lengths = {number: json.loads(payload)["length_m"] for number, payload
                       in connection.execute("SELECT number, payload FROM segments") if number in selected}
        self.assertEqual(set(lengths), selected)
        target = self.segments["sb"]
        intervals = [EnvironmentInterval("sb", 0, 50, 50, (1, 0, 0, 0), (1, 1)),
                     EnvironmentInterval("sb", 50, 100, 50, (0, 0, None, 0), (1, 0))]
        prepared = [(number, [(0, length, 1, 0, 0, 0, 1, 1)]) for number, length in lengths.items()
                    if number != target]
        prepared.append((target, [(0, 50, 1, 0, 0, 0, 1, 1),
                                  (50, 100, 0, 0, math.nan, 0, 1, 0)]))
        with self.assertRaisesRegex(ValueError, "lacks a selected segment"):
            self.graph.native.set_environment_intervals([])
        with self.assertRaisesRegex(ValueError, "Invalid prepared environment"):
            self.graph.native.set_environment_intervals([(target, [(0, 100, 2, 0, 0, 0, 1, 1)])])
        shortened = prepared[:-1] + [(target, [(0, 90, 1, 0, 0, 0, 1, 1)])]
        with self.assertRaisesRegex(ValueError, "chainage does not match"):
            self.graph.native.set_environment_intervals(shortened)
        self.graph.native.set_environment_intervals(prepared)
        start, end = self._segment_endpoint("sb", 25), self._segment_endpoint("sb", 75)
        weights = EnvironmentWeights(greenery=0.5, traffic=0.25)
        expected = evaluate_environment_cost(intervals, 25, 75, weights)
        for first, last in ((start, end), (end, start)):
            path = numeric_shortest_path(self.graph, first, last, environment_weights=weights)
            self.assertEqual(path.segment_ids.tolist(), [target])
            self.assertAlmostEqual(path.cost, 50 + expected.greenery_m + expected.traffic_m)
            np.testing.assert_allclose(path.environment_costs[0], [expected.greenery_m, 0,
                                                                   expected.traffic_m, 0])
            np.testing.assert_allclose(path.environment_unknown_m[0], expected.unknown_graph_m)
        off = numeric_shortest_path(self.graph, start, end)
        self.assertEqual(off.cost, 50)
        np.testing.assert_array_equal(off.environment_costs, np.zeros((1, 4)))
        keys = {number: key for key, number in self.segments.items()}
        environment = {
            keys[number]: tuple(EnvironmentInterval(keys[number], row[0], row[1], row[1] - row[0],
                tuple(None if math.isnan(value) else value for value in row[2:6]), tuple(row[6:8]))
                for row in rows)
            for number, rows in prepared
        }
        with closing(open_network_database(self.reference)) as connection:
            local = load_local_network(connection, BOUNDS)
        home, turnaround = self._snap("sb", 25, 375025), self._snap("sb", 75, 375075)
        points = projected_points([[snap.longitude, snap.latitude] for snap in (home, turnaround)])
        for mode in ("one_way", "round_trip"):
            baseline = plan_run(local.routing, home, turnaround, RunSettings(avoidance_ratio=0), mode=mode)
            disabled = plan_run(local.routing, home, turnaround, RunSettings(avoidance_ratio=0),
                                mode=mode, environment=environment)
            for a, b in zip(baseline.candidates[0].legs, disabled.candidates[0].legs):
                self.assertEqual(a.path, b.path)
            reference = plan_run(local.routing, home, turnaround, RunSettings(avoidance_ratio=0),
                                 mode=mode, environment=environment, environment_weights=weights)
            native = numeric_plan_run(self.graph, start, end, *points, RunSettings(avoidance_ratio=0),
                                      mode=mode, environment_weights=weights)
            self.assertEqual(reference.outcomes, native.outcomes)
            for a, b in zip(reference.candidates[0].legs, native.candidates[0].legs):
                self.assertAlmostEqual(a.path.cost, b.cost)
                self.assertAlmostEqual(a.costs.environment_greenery, b.environment_costs[:, 0].sum())
                self.assertAlmostEqual(a.costs.environment_traffic, b.environment_costs[:, 2].sum())
                self.assertTrue(b.environment_prepared)
                self.assertAlmostEqual(a.observations.environment_unknown_traffic_m,
                                       b.environment_unknown_m[:, 2].sum())

    def test_environment_weight_requires_prepared_graph(self) -> None:
        with self.assertRaisesRegex(ValueError, "Environment weights require"):
            numeric_shortest_path(self.graph, self._endpoint("s"), self._endpoint("c"),
                                  environment_weights=EnvironmentWeights(greenery=1))

    def test_bounded_environment_join_is_loaded_with_numeric_graph(self) -> None:
        selected = set(np.frombuffer(self.graph.native.selected_segment_ids(), dtype="<i8").tolist())
        join_path = self.root / "environment.sqlite"
        with closing(open_network_database(self.reference)) as source, closing(sqlite3.connect(join_path)) as join:
            join.execute("""CREATE TABLE intervals (
                segment_number INTEGER, segment_id TEXT, ordinal INTEGER,
                graph_start_m REAL, graph_end_m REAL, projected_length_m REAL,
                greenery REAL, water REAL, traffic REAL, wetland REAL,
                context_support REAL, traffic_context_support REAL)""")
            for number, payload in source.execute("SELECT number, payload FROM segments"):
                segment = json.loads(payload)
                join.execute("INSERT INTO intervals VALUES (?, ?, 0, 0, ?, ?, 0, 0, 0, 0, 1, 1)",
                             (number, segment["id"], segment["length_m"], segment["length_m"]))
            join.commit()
            with closing(open_numeric_database(self.numeric)) as numeric:
                graph = load_numeric_graph(numeric, BOUNDS, environment_connection=join)
            local = load_local_network(source, BOUNDS, environment_connection=join)
        self.assertEqual(set(np.frombuffer(graph.native.selected_segment_ids(), dtype="<i8").tolist()), selected)
        self.assertGreater(graph.environment_bytes, 0)
        path = numeric_shortest_path(graph, self._segment_endpoint("sb", 25),
                                     self._segment_endpoint("sb", 75),
                                     environment_weights=EnvironmentWeights(greenery=0.5))
        self.assertEqual(path.cost, 75)
        self.assertEqual(path.environment_costs[:, 0].tolist(), [25])
        reference = plan_run(local.routing, self._snap("sb", 25, 375025), self._snap("sb", 75, 375075),
                             RunSettings(avoidance_ratio=0), mode="one_way", environment=local.environment,
                             environment_weights=EnvironmentWeights(greenery=.5))
        self.assertEqual(reference.candidates[0].legs[0].path.cost, path.cost)

    def _segment_endpoint(self, key: str, offset: float) -> NumericEndpoint:
        return NumericEndpoint(segment_id=self.segments[key], graph_offset_m=offset,
                               hill_intervals=self.hill_intervals[key])

    def _snap(self, segment: str, offset: float, x: float) -> Snap:
        lon, lat = transform("EPSG:3067", "EPSG:4326", [x], [6675000])
        return Snap(segment, offset, offset, lon[0], lat[0], 0)

    def test_distances_forward_reverse_budgets_and_profiles_match_reference(self) -> None:
        with closing(open_network_database(self.reference)) as connection:
            for profile in self.graph.metadata.models.profiles:
                reference = load_local_network(connection, BOUNDS, profile).routing
                for start in ("s", "c", "d"):
                    for reverse in (False, True):
                        for maximum in (0, 40, 100, 200, math.inf):
                            expected = shortest_distances(reference, start, maximum, reverse=reverse)
                            actual = numeric_distances(self.graph, self._endpoint(start), maximum,
                                                       profile=profile, reverse=reverse)
                            observed = {self.keys[int(n)]: float(d) for n, d in zip(actual.node_ids, actual.values)}
                            self.assertEqual(observed, expected)
                            self.assertFalse(actual.frontier_node_ids.size)
                            self.assertFalse(actual.values.flags.writeable)

    def test_partial_distance_start_and_path_endpoints_match_reference(self) -> None:
        profile = exploration_profile(avoid_stairs=False)
        snap = self._snap("sb", 25, 375025)
        start = NumericEndpoint(segment_id=self.segments["sb"], graph_offset_m=25, chainage_m=25)
        with closing(open_network_database(self.reference)) as connection:
            reference = load_local_network(connection, BOUNDS, profile).routing
            split, key = split_start_graph(reference, snap)
            for reverse in (False, True):
                expected = shortest_distances(split, key, 200, reverse=reverse)
                actual = numeric_distances(self.graph, start, 200, profile=profile, reverse=reverse)
                observed = {key if n == actual.start_node_id else self.keys[int(n)]: float(d)
                            for n, d in zip(actual.node_ids, actual.values)}
                self.assertEqual(observed, expected)
            destination = NumericEndpoint(segment_id=self.segments["bc"], graph_offset_m=75, chainage_m=75)
            path = numeric_shortest_path(self.graph, start, destination, profile=profile)
            self.assertEqual(path.cost, 150)
            np.testing.assert_equal(path.start_m, [25, 0])
            np.testing.assert_equal(path.end_m, [100, 75])
            backward = numeric_shortest_path(self.graph, destination, start, profile=profile)
            self.assertEqual(backward.cost, 150)
            np.testing.assert_equal(backward.start_m, [75, 100])
            np.testing.assert_equal(backward.end_m, [0, 25])
            same = numeric_shortest_path(self.graph, start,
                NumericEndpoint(segment_id=self.segments["sb"], graph_offset_m=75), profile=profile)
            self.assertEqual(same.cost, 50)
            self.assertEqual(same.segment_ids.tolist(), [self.segments["sb"]])
            self.assertEqual(numeric_shortest_path(self.graph, start, start, profile=profile).cost, 0)

    def test_shape_hills_reuse_and_partial_costs_match_reference(self) -> None:
        home, turnaround = self._snap("sb", 25, 375025), self._snap("bc", 75, 375175)
        points = projected_points([[home.longitude, home.latitude], [turnaround.longitude, turnaround.latitude]])
        native_home = replace(self._segment_endpoint("sb", 25), chainage_m=25)
        native_turnaround = replace(self._segment_endpoint("bc", 75), chainage_m=75)
        profile = exploration_profile(avoid_stairs=False)
        with closing(open_network_database(self.reference)) as connection:
            local = load_local_network(connection, BOUNDS, profile)
            for width in (0, .18):
                for hills, weight in (("off", 0), ("low", 1), ("high", 6)):
                    for right in (False, True):
                        settings = RunSettings(avoidance_ratio=width)
                        overlay = endpoint_overlay(local.routing, home, turnaround)
                        data = build_request_cost_data(overlay.graph, home, turnaround, settings,
                            parts=overlay.parts, preferences=PreferenceSettings(hills=hills), hills=local.hills)
                        cache = RequestCostCache()
                        used = frozenset(overlay.graph.integer.segment_indices.values())
                        leg = LegCostContext(right, frozenset(used))
                        expected = shortest_path(overlay.graph, overlay.start, overlay.destination,
                            indexed_arc_cost=lambda arc: indexed_arc_cost(arc, data=data, cache=cache, leg=leg, deadline=None))
                        breakdown = path_cost_breakdown(expected.arc_ids, data, cache, leg)
                        shape = NumericShapeCosts(points[0], points[1], settings, right,
                                                  tuple(self.segments.values()))
                        actual = numeric_shortest_path(self.graph, native_home, native_turnaround,
                            profile=profile, hill_weight=weight, shape=shape)
                        self.assertAlmostEqual(actual.cost, expected.cost, delta=1e-4)
                        np.testing.assert_allclose(actual.components.sum(axis=0),
                            [breakdown.distance_m, breakdown.centre, breakdown.side, breakdown.reuse, breakdown.hills], atol=1e-5)
                        if weight:
                            features = [cache.hill_features[overlay.graph.integer.arc_indices[key]] for key in expected.arc_ids]
                            expected_hills = [[f.penalty_m, f.ascent_m, f.descent_m, f.valid_length_m,
                                               f.trusted_length_m, f.fallback_length_m, f.length_m] for f in features]
                            np.testing.assert_allclose(actual.hill_features, expected_hills, atol=1e-8)
                        self.assertFalse(actual.arc_ids.flags.writeable)

    def test_selected_windows_filter_extra_chunks_and_report_frontier(self) -> None:
        bounds = (375040, 6674990, 375060, 6675010)
        with closing(open_numeric_database(self.numeric)) as connection:
            payloads = [stored.payload for stored in iter_numeric_chunks(connection, BOUNDS)]
        native = _routing_native.NumericGraph(payloads, bounds=bounds)
        graph = replace(self.graph, native=native, bounds=bounds)
        self.assertEqual(graph.native.node_count, 2)
        self.assertEqual(graph.native.segment_count, 1)
        distances = numeric_distances(graph, self._endpoint("s"))
        self.assertEqual(set(distances.node_ids), {self.nodes["s"], self.nodes["b"]})
        self.assertEqual(distances.frontier_node_ids.tolist(), [self.nodes["b"]])
        with self.assertRaises(ValueError):
            numeric_distances(graph, self._endpoint("c"))
        limited = numeric_distances(graph, self._endpoint("s"), 50)
        self.assertFalse(limited.frontier_node_ids.size)

    def test_partial_span_reuse_does_not_penalize_other_parts(self) -> None:
        home = NumericEndpoint(segment_id=self.segments["sb"], graph_offset_m=25)
        turnaround = NumericEndpoint(segment_id=self.segments["bc"], graph_offset_m=75)
        settings = RunSettings(avoidance_ratio=.18, centre_enabled=False, side_strength=0)
        shape = NumericShapeCosts((375025, 6675000), (375175, 6675000), settings, False,
                                  used_spans=((self.segments["sb"], 25, 100),))
        returning = numeric_shortest_path(self.graph, turnaround, home, shape=shape)
        self.assertEqual(returning.cost, 450)
        self.assertEqual(returning.components[:, 3].tolist(), [0, 300])

    def test_native_run_candidates_match_reference_costs_and_preferences(self) -> None:
        home, turnaround = self._snap("sb", 25, 375025), self._snap("bc", 75, 375175)
        points = projected_points([[home.longitude, home.latitude], [turnaround.longitude, turnaround.latitude]])
        start = replace(self._segment_endpoint("sb", 25), chainage_m=25)
        end = replace(self._segment_endpoint("bc", 75), chainage_m=75)
        with closing(open_network_database(self.reference)) as connection:
            local = load_local_network(connection, BOUNDS)
            for width in (0, .18):
                for level in ("off", "low", "high"):
                    settings, preferences = RunSettings(avoidance_ratio=width), PreferenceSettings(hills=level)
                    actual = numeric_plan_run(self.graph, start, end, points[0], points[1], settings,
                                              preferences=preferences)
                    expected = plan_run(local.routing, home, turnaround, settings,
                                        preferences=preferences, hills=local.hills)
                    self.assertEqual(actual.outcomes, expected.outcomes)
                    self.assertEqual(len(actual.candidates), len(expected.candidates))
                    for a, e in zip(actual.candidates, expected.candidates):
                        self.assertEqual(a.outbound_left, e.outbound_left)
                        self.assertAlmostEqual(a.legs[0].cost, e.legs[0].path.cost, delta=1e-4)
                        self.assertAlmostEqual(a.legs[1].cost, e.legs[1].path.cost, delta=1e-4)

    def test_same_segment_partial_hills_match_reference_in_both_directions(self) -> None:
        profile = exploration_profile(avoid_stairs=False)
        with closing(open_network_database(self.reference)) as connection:
            local = load_local_network(connection, BOUNDS, profile)
            for low, high in ((25, 75), (75, 25), (25, 25), (0, 100)):
                home, end = self._snap("sb", low, 375000 + low), self._snap("sb", high, 375000 + high)
                if low != high:
                    overlay = endpoint_overlay(local.routing, home, end)
                    request = build_request_cost_data(overlay.graph, home, end, RunSettings(avoidance_ratio=0),
                        parts=overlay.parts, preferences=PreferenceSettings(hills="high"), hills=local.hills)
                    cache, leg = RequestCostCache(), LegCostContext(True)
                    expected = shortest_path(overlay.graph, overlay.start, overlay.destination,
                        indexed_arc_cost=lambda arc: indexed_arc_cost(arc, data=request, cache=cache, leg=leg, deadline=None))
                    expected_cost = expected.cost
                else:
                    expected_cost = 0
                actual = numeric_shortest_path(self.graph, self._segment_endpoint("sb", low),
                    self._segment_endpoint("sb", high), profile=profile, hill_weight=6)
                self.assertAlmostEqual(actual.cost, expected_cost, places=8)

    def test_partial_hills_reject_missing_invalid_and_incomplete_detail(self) -> None:
        start, end = self._segment_endpoint("sb", 25), self._segment_endpoint("sb", 75)
        for payload in (None, b"bad", bytes(128), np.full((1, 16), math.nan, dtype="<f8").tobytes()):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                numeric_shortest_path(self.graph, replace(start, hill_intervals=payload),
                    replace(end, hill_intervals=payload), hill_weight=6)
        intervals = np.frombuffer(start.hill_intervals, dtype="<f8").copy().reshape(-1, 16)
        intervals[-1, 1] = 99
        with self.assertRaisesRegex(ValueError, "do not cover"):
            numeric_shortest_path(self.graph, replace(start, hill_intervals=intervals.tobytes()),
                replace(end, hill_intervals=intervals.tobytes()), hill_weight=6)
        # Full-segment traversal uses totals without requiring interval detail.
        self.assertIsNotNone(numeric_shortest_path(self.graph, self._endpoint("s"), self._endpoint("b"),
                                                  hill_weight=6).cost)

    def test_legacy_embedded_intervals_remain_usable(self) -> None:
        with closing(open_network_database(self.reference)) as connection:
            payloads = [encode_numeric_chunk(prepare_numeric_chunk(connection, cast(TopologyChunk, json.loads(row[0])),
                        self.graph.metadata.models.profiles)) for row in connection.execute("SELECT payload FROM graph_chunks ORDER BY number")]
        legacy = replace(self.graph, native=_routing_native.NumericGraph(payloads, bounds=BOUNDS,
                         distance_scale=self.graph.metadata.distance_scale))
        home, end = self._segment_endpoint("sb", 25), self._segment_endpoint("bc", 75)
        actual = numeric_shortest_path(self.graph, home, end, hill_weight=6)
        original = numeric_shortest_path(legacy, replace(home, hill_intervals=None),
                                         replace(end, hill_intervals=None), hill_weight=6)
        self.assertEqual(actual.arc_ids.tolist(), original.arc_ids.tolist())
        np.testing.assert_allclose(actual.components, original.components, atol=1e-10, rtol=1e-12)
        np.testing.assert_allclose(actual.hill_features, original.hill_features, atol=1e-10, rtol=1e-12)

    def test_unreachable_deadlines_and_invalid_inputs(self) -> None:
        path = numeric_shortest_path(self.graph, self._endpoint("s"), self._endpoint("d"))
        self.assertIsNone(path.cost)
        self.assertFalse(path.arc_ids.size)
        for endpoint in (NumericEndpoint(), NumericEndpoint(node_id=self.nodes["s"], segment_id=self.segments["sb"]),
                         NumericEndpoint(segment_id=self.segments["sb"], graph_offset_m=-1),
                         NumericEndpoint(segment_id=self.segments["sb"], chainage_m=1e10)):
            with self.assertRaises(ValueError):
                numeric_distances(self.graph, endpoint)
        for budget in (-1, math.nan):
            with self.assertRaises(ValueError):
                numeric_distances(self.graph, self._endpoint("s"), budget)
        with self.assertRaises(TimeoutError):
            numeric_distances(self.graph, self._endpoint("s"), deadline=monotonic()-1)
        with self.assertRaises(TimeoutError):
            self.graph.native.distances(self.nodes["s"], timeout_seconds=0)
        with self.assertRaises(ValueError):
            numeric_shortest_path(self.graph, self._endpoint("s"), self._endpoint("c"), hill_weight=math.nan)

    def test_graph_is_shared_between_concurrent_searches_without_python_callbacks(self) -> None:
        with patch("runmap.routing_reference.search.shortest_path", side_effect=AssertionError("Python search")), \
                patch("runmap.routing_reference.costs.indexed_arc_cost", side_effect=AssertionError("Python costs")), \
                patch.object(json, "loads", side_effect=AssertionError("record decode")):
            def request(index: int) -> float | None:
                path = numeric_shortest_path(self.graph, self._endpoint("s"), self._endpoint("c"))
                return path.cost
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(request, range(16)))
        self.assertEqual(results, [200] * 16)

    def test_one_way_routes_and_excluded_endpoints(self) -> None:
        root = self.root / "restricted"
        root.mkdir()
        source, graph, _ = publish_fixture(root, terrain=False)
        graph["segments"][1]["tags"] = {"highway": "path", "oneway:foot": "-1"}
        graph["nodes"][-1]["foot"] = "no"
        source.write_text(json.dumps(graph))
        reference, numeric = root / "network.sqlite", root / "numeric.sqlite"
        export_network_database(source, reference)
        export_numeric_database(reference, numeric)
        with closing(open_numeric_database(numeric)) as connection:
            loaded = load_numeric_graph(connection, BOUNDS)
        self.assertIsNone(numeric_shortest_path(loaded, self._endpoint("s"), self._endpoint("c")).cost)
        self.assertEqual(numeric_shortest_path(loaded, self._endpoint("c"), self._endpoint("s")).cost, 200)
        with self.assertRaisesRegex(ValueError, "not in the selected graph"):
            numeric_distances(loaded, self._endpoint("e"))
        with self.assertRaisesRegex(ValueError, "not in the selected graph"):
            numeric_shortest_path(loaded, self._endpoint("e"), self._endpoint("e"))

    def test_native_constructor_rejects_corrupt_references_and_duplicate_chunks(self) -> None:
        with closing(open_numeric_database(self.numeric)) as connection:
            payloads = [stored.payload for stored in iter_numeric_chunks(connection, BOUNDS)]
        for payload in (b"", payloads[0][:-1], b"RUNNUM01" + payloads[0][8:]):
            with self.assertRaises(ValueError):
                _routing_native.NumericGraph([payload])
        with self.assertRaises(ValueError):
            _routing_native.NumericGraph([payloads[0], payloads[0]])
        with self.assertRaises(TypeError):
            _routing_native.NumericGraph([bytearray(payloads[0])])
        original = payloads[0]
        decoded = decode_numeric_chunk(original)
        origin = np.frombuffer(original, dtype="u1").ctypes.data
        for name in ("arc_nodes", "outgoing_arcs", "geometry_offsets"):
            payload = bytearray(original)
            offset = decoded.arrays[name].ctypes.data-origin
            payload[offset:offset+8] = (10**12).to_bytes(8, "little")
            with self.subTest(column=name), self.assertRaises(ValueError):
                _routing_native.NumericGraph([bytes(payload)])
        with self.assertRaises(TimeoutError):
            _routing_native.NumericGraph(payloads, timeout_seconds=0)

    def test_hills_and_shape_choose_alternative_routes_like_reference(self) -> None:
        root = self.root / "alternatives"
        root.mkdir()
        xy = [(375000, 6675000), (375100, 6675000), (375050, 6675100), (375050, 6674900)]
        lon, lat = transform("EPSG:3067", "EPSG:4326", *zip(*xy))
        nodes = [(key, x, y) for key, x, y in zip(("a", "b", "c", "d"), lon, lat)]
        graph = graph_document(nodes, [(key, a, b, length, 0, {"highway": "path"})
            for key, a, b, length in (("ab", "a", "b", 100), ("ac", "a", "c", 112),
                                    ("cb", "c", "b", 112), ("ad", "a", "d", 112), ("db", "d", "b", 112))])
        graph["summary"] = {}
        for segment in graph["segments"]:
            segment["osm_way_id"] = segment["id"]
        enrich(graph, lambda i, record: (.2 if record.segment_id == "ab" else 0, 90))
        source = write_graph_outputs(graph, root)["graph"]
        reference, numeric = root / "network.sqlite", root / "numeric.sqlite"
        with patch("runmap.paths.graph_chunks.MAX_CHUNK_SEGMENTS", 1):
            export_network_database(source, reference)
        export_numeric_database(reference, numeric)
        with closing(open_numeric_database(numeric)) as connection:
            loaded = load_numeric_graph(connection, BOUNDS)
            ids = dict(connection.execute("SELECT external_id, number FROM identities WHERE kind='node'"))
            segment_ids = dict(connection.execute(
                "SELECT external_id, number FROM identities WHERE kind='segment'"))
        with closing(open_network_database(reference)) as connection:
            local = load_local_network(connection, BOUNDS)
        home, turnaround = Snap("ab", 0, 0, lon[0], lat[0], 0), Snap("ab", 100, 100, lon[1], lat[1], 0)
        for mode in ("one_way", "round_trip"):
            for width in (0, .18):
                for level in ("off", "high"):
                    settings, preferences = RunSettings(avoidance_ratio=width), PreferenceSettings(hills=level)
                    expected = plan_run(local.routing, home, turnaround, settings, preferences=preferences, hills=local.hills, mode=mode)
                    actual = numeric_plan_run(loaded, NumericEndpoint(node_id=ids["a"]), NumericEndpoint(node_id=ids["b"]),
                        xy[0], xy[1], settings, preferences=preferences, mode=mode)
                    self.assertEqual(actual.outcomes, expected.outcomes)
                    for a, b in zip(actual.candidates, expected.candidates):
                        self.assertAlmostEqual(a.legs[0].cost, b.legs[0].path.cost, delta=1e-3)
                        if mode == "round_trip":
                            self.assertAlmostEqual(a.legs[1].cost, b.legs[1].path.cost, delta=1e-3)
                        if (width == 0 or mode == "one_way") and level == "high":
                            self.assertGreater(len(a.legs[0].arc_ids), 1)
        with closing(open_network_database(reference)) as connection:
            lengths = {number: json.loads(payload)["length_m"] for number, payload
                       in connection.execute("SELECT number, payload FROM segments")}
        loaded.native.set_environment_intervals([
            (number, [(0, length, 0 if number == segment_ids["ab"] else 1,
                       0, 0, 0, 1, 1)]) for number, length in lengths.items()
        ])
        start, destination = NumericEndpoint(node_id=ids["a"]), NumericEndpoint(node_id=ids["b"])
        direct = numeric_plan_run(loaded, start, destination, xy[0], xy[1],
                                  RunSettings(avoidance_ratio=0), mode="one_way")
        avoided = numeric_plan_run(loaded, start, destination, xy[0], xy[1],
                                   RunSettings(avoidance_ratio=0), mode="one_way",
                                   environment_weights=EnvironmentWeights(greenery=2))
        self.assertEqual(direct.candidates[0].legs[0].cost, 100)
        self.assertEqual(avoided.candidates[0].legs[0].cost, 224)
        self.assertNotIn(segment_ids["ab"], avoided.candidates[0].legs[0].segment_ids)
        environment = {
            key: (EnvironmentInterval(key, 0, lengths[number], lengths[number],
                    (0 if key == "ab" else 1, 0, 0, 0), (1, 1)),)
            for key, number in segment_ids.items()
        }
        for mode in ("one_way", "round_trip"):
            settings, preferences = RunSettings(avoidance_ratio=.18), PreferenceSettings(hills="high")
            expected = plan_run(local.routing, home, turnaround, settings, mode=mode,
                                hills=local.hills, preferences=preferences,
                                environment=environment, environment_weights=EnvironmentWeights(greenery=2))
            actual = numeric_plan_run(loaded, start, destination, xy[0], xy[1], settings,
                                      mode=mode, preferences=preferences,
                                      environment_weights=EnvironmentWeights(greenery=2))
            self.assertEqual(expected.outcomes, actual.outcomes)
            for candidate_a, candidate_b in zip(expected.candidates, actual.candidates):
                for leg_a, leg_b in zip(candidate_a.legs, candidate_b.legs):
                    self.assertAlmostEqual(leg_a.path.cost, leg_b.cost, delta=1e-3)

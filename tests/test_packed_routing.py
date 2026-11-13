"""Packed inputs preserve route results and survive shared-store eviction."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import fields, replace
import json
from time import monotonic
import unittest
from unittest.mock import patch

import numpy as np
from rasterio.warp import transform

from runmap.api.runs import PreferencesRequest
from runmap.api.numeric_data import NumericRoutingData
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import generate_database_run
from runmap.environment_fields import EnvironmentField
from runmap.paths.database import network_metadata
from runmap.paths.exploration import Snap
from runmap.paths.routing_inputs import export_routing_inputs, read_input_payload
from runmap.paths.compact_routing import compact_routing
from runmap.routing.environment_costs import EnvironmentWeights
from runmap.routing.input_store import SharedInputStore
from runmap.routing.numeric_search import (LoadedNumericGraph, NumericEndpoint, NumericPath,
                                           load_numeric_graph, numeric_shortest_path)
from runmap.routing.numeric_store import read_numeric_payload
from runmap.routing.prepared_inputs import InputRequirements
import test_prepared_inputs
import test_numeric_search
import test_numeric_store
import test_routing_inputs
from test_database_features import BOUNDS


class PackedRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = test_prepared_inputs.PreparedInputTests("test_channel_changes_do_not_reuse_incompatible_graphs")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.numeric = self.fixture.database.with_name("numeric-routing-v2.sqlite")
        self.packed = self.numeric.with_name("routing-inputs-v1.sqlite")

    def test_channels_match_legacy_with_partial_reverse_and_changed_weights(self) -> None:
        preferences = [PreferencesRequest(), PreferencesRequest(hills="high"),
            PreferencesRequest(environment_greenery="low"), PreferencesRequest(environment_greenery="high"),
            PreferencesRequest(signals="high"),
            PreferencesRequest(hills="high", environment_greenery="low", signals="low")]
        requests = [self.fixture.request.model_copy(update={"preferences": prefs, "mode": mode})
                    for prefs in preferences for mode in ("round_trip", "one_way")]
        requests += [request.model_copy(update={"home": request.turnaround, "turnaround": request.home})
                     for request in requests if request.mode == "one_way"]
        expected = [generate_database_run(self.fixture.data, request) for request in requests]
        export_routing_inputs(self.numeric, self.packed, environment_path=self.fixture.environment,
                              signal_path=self.numeric.with_name("signals.sqlite"))
        with closing(SQLitePathData(self.fixture.database, environment_path=self.fixture.environment,
                                    environment_raster_bundle=self.fixture.bundle)) as data:
            assert data.numeric is not None and data.numeric.input_store is not None
            with patch("runmap.routing.numeric_search._attach_environment", side_effect=AssertionError("legacy environment")), \
                    patch("runmap.routing.numeric_search.read_signals", side_effect=AssertionError("legacy signals")), \
                    patch("runmap.api.numeric_data.endpoint_hill_detail", side_effect=AssertionError("legacy hills")):
                for request, baseline in zip(requests, expected):
                    actual = generate_database_run(data, request)
                    self.assertEqual(actual.status, "complete")
                    self.assertEqual(actual.model_dump(exclude={"elapsed_s"}),
                                     baseline.model_dump(exclude={"elapsed_s"}))
            self.assertLessEqual(data.numeric.resident_bytes + data.numeric._shared_bytes(), data.numeric.cache_bytes)
            data.numeric.cache_bytes = 0
            self.assertEqual(generate_database_run(data, requests[-1]).status, "complete")
            self.assertFalse(data.numeric.input_store.chunks)
            self.assertFalse(data.numeric.chunk_store.chunks)
            self.assertFalse(data.numeric.graphs)

    def test_input_cache_reads_each_chunk_once_and_filters_channels(self) -> None:
        export_routing_inputs(self.numeric, self.packed, environment_path=self.fixture.environment,
                              signal_path=self.numeric.with_name("signals.sqlite"))
        with closing(SQLitePathData(self.fixture.database, environment_path=self.fixture.environment,
                                    environment_raster_bundle=self.fixture.bundle)) as data:
            assert data.numeric is not None and data.numeric.input_store is not None
            store = data.numeric.input_store
            bounds = (-1e9, -1e9, 1e9, 1e9)
            with patch("runmap.routing.input_store.read_input_payload", wraps=read_input_payload) as reader:
                self.assertEqual(store.load(bounds, InputRequirements(), None), [])
                self.assertEqual(reader.call_count, 0)
                chunks = store.load(bounds, InputRequirements(environment=True), None)
                self.assertGreater(len(chunks), 0)
                count = reader.call_count
                self.assertEqual(store.load(bounds, InputRequirements(environment=True), None), chunks)
                self.assertEqual(reader.call_count, count)
                self.assertEqual({key[1] for key in store.chunks}, {2})
                store.load(bounds, InputRequirements(signals=True), None)
                self.assertEqual(reader.call_count, count * 2)
            with self.assertRaises(TimeoutError):
                store.load(bounds, InputRequirements(environment=True), monotonic()-1)
            store.resize(0)
            self.assertEqual(store.resident_bytes, 0)

    def test_topology_only_generation_matches_all_channel_and_off_responses(self) -> None:
        requests = [self.fixture.request.model_copy(update={"preferences": prefs, "mode": mode})
                    for prefs in (PreferencesRequest(), PreferencesRequest(hills="high"),
                        PreferencesRequest(hills="high", environment_greenery="low", signals="low"))
                    for mode in ("round_trip", "one_way")]
        expected = [generate_database_run(self.fixture.data, request) for request in requests]
        export_routing_inputs(self.numeric, self.packed, environment_path=self.fixture.environment,
                              signal_path=self.numeric.with_name("signals.sqlite"))
        output = self.numeric.parent / "compact"
        compact_routing(self.numeric, self.packed, output, environment_path=self.fixture.environment,
                        signal_path=self.numeric.with_name("signals.sqlite"))
        # Keep default artifact discovery under test, including precedence over version 2.
        (output / "network.sqlite").symlink_to(self.fixture.database)
        (output / "signals.sqlite").symlink_to(self.numeric.with_name("signals.sqlite"))
        with closing(SQLitePathData(output / "network.sqlite", environment_path=self.fixture.environment,
                                    environment_raster_bundle=self.fixture.bundle)) as data:
            assert data.numeric is not None
            self.assertEqual(data.numeric.connection.execute("PRAGMA user_version").fetchone()[0], 3)
            for request, baseline in zip(requests, expected):
                actual = generate_database_run(data, request)
                self.assertEqual(actual.status, "complete")
                self.assertEqual(actual.model_dump(exclude={"elapsed_s"}), baseline.model_dump(exclude={"elapsed_s"}))


class PackedHillTests(unittest.TestCase):
    def test_same_bounds_changed_endpoints_keep_independent_hill_wrappers(self) -> None:
        fixture = test_numeric_search.NumericSearchTests("test_legacy_embedded_intervals_remain_usable")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        export_routing_inputs(fixture.numeric, fixture.numeric.with_name("routing-inputs-v1.sqlite"))
        requirements = InputRequirements(hills=True)
        with closing(NumericRoutingData(fixture.numeric, fixture.graph.metadata)) as data:
            first, start, end = data.prepare(BOUNDS,
                (fixture._snap("sb", 25, 375025), fixture._snap("sb", 75, 375075)),
                monotonic()+5, requirements=requirements)
            original_detail = dict(first.hill_intervals)
            with patch("runmap.api.numeric_data.load_numeric_graph", side_effect=AssertionError("cache miss")):
                second, other_start, other_end = data.prepare(BOUNDS,
                    (fixture._snap("bc", 25, 375125), fixture._snap("bc", 75, 375175)),
                    monotonic()+5, requirements=requirements)
            self.assertIsNot(first, second)
            self.assertIs(first.native, second.native)
            self.assertIsNot(first.hill_intervals, second.hill_intervals)
            self.assertEqual(set(first.hill_intervals), {fixture.segments["sb"]})
            self.assertEqual(set(second.hill_intervals), {fixture.segments["bc"]})
            self.assertEqual(first.hill_intervals, original_detail)
            self.assertFalse(next(iter(data.graphs.values()))[0].hill_intervals)
            for graph, a, b in ((first, start, end), (second, other_start, other_end)):
                actual = numeric_shortest_path(graph, a, b, hill_weight=6)
                expected = numeric_shortest_path(fixture.graph, a, b, hill_weight=6)
                self.assertIsNotNone(actual.cost)
                self.assertEqual(actual.cost, expected.cost)
                np.testing.assert_array_equal(actual.arc_ids, expected.arc_ids)
                np.testing.assert_array_equal(actual.hill_features, expected.hill_features)

    def test_off_requests_retain_half_budget_of_topology_with_packed_inputs(self) -> None:
        fixture = test_numeric_search.NumericSearchTests("test_legacy_embedded_intervals_remain_usable")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        export_routing_inputs(fixture.numeric, fixture.numeric.with_name("routing-inputs-v1.sqlite"))
        with closing(NumericRoutingData(fixture.numeric, fixture.graph.metadata)) as data:
            self.assertEqual(data.chunk_store.cache_bytes, data.cache_bytes // 2)
            data.chunk_store.load(test_numeric_store.EXPANDED, None)
            self.assertEqual(len(data.chunk_store.chunks), 2)
            topology_bytes = data.chunk_store.resident_bytes
            data.cache_bytes = 2 * topology_bytes
            data.chunk_store.clear()
            first = fixture._snap("sb", 25, 375025)
            other = fixture._snap("bc", 25, 375125)
            moved = (375140, 6674990, 375160, 6675010)
            with patch("runmap.routing.numeric_store.read_numeric_payload", wraps=read_numeric_payload) as reader, \
                    patch("runmap.routing.input_store.read_input_payload", side_effect=AssertionError("Off input load")):
                data.prepare_start(test_numeric_store.SMALL, first, monotonic()+5)
                retained = next(iter(data.chunk_store.chunks.values()))
                data.prepare_start(moved, other, monotonic()+5)
                self.assertEqual(len(data.chunk_store.chunks), 2)
                self.assertIn(retained, data.chunk_store.chunks.values())
                data.prepare_start(test_numeric_store.EXPANDED, first, monotonic()+5)
                self.assertEqual(reader.call_count, 2)
            self.assertEqual(data.chunk_store.cache_bytes, topology_bytes)
            self.assertGreater(data.chunk_store.resident_bytes, data.cache_bytes // 4)
            self.assertLessEqual(data.resident_bytes + data._shared_bytes(), data.cache_bytes)
            assert data.input_store is not None
            self.assertEqual(data.input_store.resident_bytes, 0)

    def test_endpoint_has_only_location_and_packed_hills_match_legacy_after_close(self) -> None:
        self.assertNotIn("hill_intervals", {item.name for item in fields(NumericEndpoint)})
        fixture = test_numeric_search.NumericSearchTests("test_legacy_embedded_intervals_remain_usable")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        packed = fixture.numeric.with_name("routing-inputs-v1.sqlite")
        export_routing_inputs(fixture.numeric, packed)
        from runmap.paths.numeric_database import open_numeric_database
        from runmap.paths.routing_inputs import open_routing_inputs

        with closing(open_numeric_database(fixture.numeric)) as numeric:
            with closing(open_routing_inputs(packed, numeric)) as inputs:
                store = SharedInputStore(numeric, inputs, 1024**2)
                graph = load_numeric_graph(numeric, BOUNDS)
                graph.native.set_input_chunks(store.load(BOUNDS, InputRequirements(hills=True), None))
                pairs = [(fixture._segment_endpoint("sb", a), fixture._segment_endpoint("sb", b))
                         for a, b in ((25, 75), (75, 25), (0, 100), (100, 0))]
                graph = replace(graph, hill_intervals=store.partial_hills((fixture.segments["sb"],), None))
                expected = [numeric_shortest_path(fixture.graph, a, b, hill_weight=6) for a, b in pairs]
                store.resize(0)
        def search(index: int) -> None:
            a, b = pairs[index]
            actual = numeric_shortest_path(graph, a, b, hill_weight=6)
            self.assertEqual(actual.cost, expected[index].cost)
            np.testing.assert_array_equal(actual.arc_ids, expected[index].arc_ids)
            np.testing.assert_array_equal(actual.hill_features, expected[index].hill_features)
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(search, range(len(pairs))))


class PackedInputLifetimeTests(unittest.TestCase):
    def test_overlapping_environment_signal_graphs_survive_eviction_and_close(self) -> None:
        fixture = test_routing_inputs.RoutingInputTests("test_deterministic_channels_and_exact_environment_signal_protocol")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture._export(fixture.numeric_path.with_name("routing-inputs-v1.sqlite"))
        bundle = fixture.root / "bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text(json.dumps({
            "dataset_id": "test-raster", "artifacts": [{"sha256": "raster-digest"}]}))
        weights = EnvironmentWeights(greenery=.5)
        requirements = InputRequirements(environment=True, signals=True)
        longitude, latitude = transform("EPSG:3067", "EPSG:4326", [375000], [6675000])
        snap = Snap("sb", 0, 0, longitude[0], latitude[0], 0)
        cases: list[tuple[LoadedNumericGraph, NumericEndpoint, NumericEndpoint, NumericPath]] = []
        with closing(NumericRoutingData(fixture.numeric_path, network_metadata(fixture.numeric),
                     environment_path=fixture.environment_path, raster_bundle=bundle,
                     signal_path=fixture.signal_path)) as data:
            assert data.input_store is not None
            with patch("runmap.routing.input_store.read_input_payload", wraps=read_input_payload) as reader:
                for bounds, destination in ((test_numeric_store.SMALL, "b"),
                                            (test_numeric_store.EXPANDED, "c")):
                    graph, _, _ = data.prepare(bounds, (snap, snap), monotonic()+5, requirements=requirements)
                    self.assertTrue(graph.native.environment_prepared)
                    self.assertTrue(graph.native.signals_prepared)
                    legacy = load_numeric_graph(fixture.numeric, bounds,
                        environment_connection=fixture.environment, signal_connection=fixture.signals)
                    start = NumericEndpoint(node_id=fixture.nodes["s"])
                    end = NumericEndpoint(node_id=fixture.nodes[destination])
                    expected = numeric_shortest_path(legacy, start, end,
                        environment_weights=weights, signal_weight=60)
                    self.assertIsNotNone(expected.cost)
                    self.assertGreater(expected.environment_costs[EnvironmentField.GREENERY].sum(), 0)
                    assert expected.signal_counts is not None
                    self.assertGreater(expected.signal_counts.sum(), 0)
                    cases.append((graph, start, end, expected))
                    if destination == "b":
                        self.assertEqual(reader.call_count, 2)
                        retained = tuple(data.input_store.chunks)
                        # Force eviction on expansion while keeping a positive input budget.
                        data.cache_bytes = 2 * data.input_store.resident_bytes
                self.assertEqual(reader.call_count, 4)
            self.assertIsNot(cases[0][0].native, cases[1][0].native)
            self.assertTrue(any(key not in data.input_store.chunks for key in retained))
            self.assertLessEqual(data.resident_bytes + data._shared_bytes(), data.cache_bytes)
            data.input_store.clear()
            data.chunk_store.clear()
        self.assertFalse(data.graphs)
        self.assertFalse(data.input_store.chunks)
        self.assertFalse(data.chunk_store.chunks)

        def search(index: int) -> None:
            graph, start, end, expected = cases[index]
            actual = numeric_shortest_path(graph, start, end, environment_weights=weights, signal_weight=60)
            self.assertEqual(actual.cost, expected.cost)
            np.testing.assert_array_equal(actual.arc_ids, expected.arc_ids)
            np.testing.assert_array_equal(actual.signal_counts, expected.signal_counts)
            for field in EnvironmentField:
                np.testing.assert_array_equal(actual.environment_costs[field], expected.environment_costs[field])
                np.testing.assert_array_equal(actual.environment_unknown_m[field], expected.environment_unknown_m[field])

        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(search, (0, 1, 0, 1)))

"""API planning uses native buffers and hydrates only chosen routes."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from runmap.api.numeric_data import NumericRoutingData, read_hill_intervals
from runmap.api.paths import load_path_data
from runmap.api.runs import PreferencesRequest, SettingsRequest, RunResponse
from runmap.routing_reference.api import generate_run
from runmap.api.app import app, run_route
from starlette.requests import Request
from fastapi import HTTPException
from test_measurements import enrich
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import RunAreaLimits, generate_database_run
from runmap.paths.numeric_database import export_numeric_database
from runmap.routing.numeric_planner import NumericRunResult, numeric_plan_run
from runmap.routing.numeric_search import LoadedNumericGraph, NumericEndpoint, Point
from runmap.routing.run_types import RunMode, RunSettings
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.types import EdgeProfile, GraphDocument
from test_path_api import fixture
import test_sqlite_runs
from test_sqlite_runs import detour_graph


class NumericRunTests(unittest.TestCase):
    setUp = test_sqlite_runs.SQLiteRunTests.setUp
    options = test_sqlite_runs.SQLiteRunTests.options
    test_one_way_succeeds_without_return_and_matches_reference = test_sqlite_runs.SQLiteRunTests.test_one_way_succeeds_without_return_and_matches_reference
    test_one_way_expands_detours_and_reports_area_and_deadline_limits = test_sqlite_runs.SQLiteRunTests.test_one_way_expands_detours_and_reports_area_and_deadline_limits
    test_one_way_policies_and_reversed_partial_endpoints = test_sqlite_runs.SQLiteRunTests.test_one_way_policies_and_reversed_partial_endpoints

    def publish(self, graph: GraphDocument) -> tuple[SQLitePathData, Path]:
        data, source = test_sqlite_runs.SQLiteRunTests.publish(self, graph)
        database = source.with_name("network.sqlite")
        numeric = source.with_name("numeric-routing-v2.sqlite")
        export_numeric_database(database, numeric)
        data.close()
        data = SQLitePathData(database)
        self.addCleanup(data.close)
        self.assertIsNotNone(data.numeric)
        return data, source

    def test_native_costs_geometry_preferences_and_policies_match(self) -> None:
        graph = fixture()
        graph["segments"][0]["backward"] = True
        graph["arcs"].append({"id": "s:b", "segment_id": "s", "from": "2", "to": "1", "length_m": 220})
        data, source = self.publish(graph)
        reference = load_path_data(source)
        with patch("runmap.routing_reference.sqlite_runs.load_local_network", side_effect=AssertionError("legacy graph loaded")):
            for hills in ("off", "low", "high"):
                for avoidance in (0, .18):
                    for surface in ("all", "surfaced"):
                        for stairs in (True, False):
                            options = self.options(data).model_copy(update={"preferences": PreferencesRequest(hills=hills),
                                "settings": SettingsRequest(avoidance_ratio=avoidance), "surface": surface, "avoid_stairs": stairs})
                            actual = generate_database_run(data, options)
                            expected = generate_run(reference, options)
                            self.assertEqual(actual.status, expected.status)
                            self.assertEqual(actual.cost_model, expected.cost_model)
                            self.assertEqual(len(actual.candidates), len(expected.candidates))
                            for a, b in zip(actual.candidates, expected.candidates):
                                self.assertAlmostEqual(a.distance_m, b.distance_m, places=5)
                                self.assertAlmostEqual(a.repeated_distance_m, b.repeated_distance_m, places=5)
                                for x, y in ((a.legs[0], b.legs[0]), (a.legs[1], b.legs[1])):
                                    self.assertAlmostEqual(x.cost, y.cost, places=4)
                                    self.assertEqual(x.traversals, y.traversals)
                                    self.assertEqual(x.coordinates, y.coordinates)
                                    for key, value in y.cost_breakdown.__dict__.items():
                                        self.assertAlmostEqual(getattr(x.cost_breakdown, key), value, places=4)
                                    for key, value in y.observations.__dict__.items():
                                        if value is None:
                                            self.assertIsNone(getattr(x.observations, key))
                                        else:
                                            self.assertAlmostEqual(getattr(x.observations, key), value, places=4)

    def test_expansion_limits_source_coverage_and_unreachable(self) -> None:
        for connected, bent in ((True, False), (True, True), (False, False)):
            data, source = self.publish(detour_graph(connected=connected, bent_segment=bent))
            options = self.options(data, detour=True)
            actual = generate_database_run(data, options)
            expected = generate_run(load_path_data(source), options)
            self.assertEqual(actual.status, expected.status)
            if connected:
                self.assertGreater(actual.search.retries, 0)
                self.assertAlmostEqual(actual.candidates[0].distance_m, expected.candidates[0].distance_m, places=4)
                for limits in (RunAreaLimits(max_retries=0), RunAreaLimits(max_area_km2=.1)):
                    limited = generate_database_run(data, options, limits=limits)
                    self.assertEqual(limited.status, "area_limited")
                    self.assertFalse(limited.candidates)
            else:
                self.assertEqual(actual.search.retries, 0)

    def test_cache_concurrency_eviction_deadlines_and_artifact_identity(self) -> None:
        data, source = self.publish(detour_graph())
        options = self.options(data, detour=True)
        first = generate_database_run(data, options)
        with patch("runmap.api.numeric_data.load_numeric_graph", side_effect=AssertionError("cache miss")):
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(lambda _: generate_database_run(data, options), range(2)))
        self.assertTrue(all(r.candidates == first.candidates for r in results))
        self.assertLessEqual(data.numeric.resident_bytes, data.numeric.cache_bytes)
        with patch("runmap.api.numeric_data.load_numeric_graph", side_effect=TimeoutError):
            data.numeric.graphs.clear()
            data.numeric.resident_bytes = 0
            self.assertEqual(generate_database_run(data, options).status, "deadline")
        with self.assertRaisesRegex(ValueError, "does not match"):
            NumericRoutingData(source.with_name("numeric-routing-v2.sqlite"), replace(data.metadata, dataset_id="stale"))
        data.numeric.cache_bytes = 0
        self.assertEqual(generate_database_run(data, options).status, "complete")
        self.assertFalse(data.numeric.graphs)

    def test_hill_details_are_request_local_and_off_reuses_the_same_graph(self) -> None:
        data, _ = self.publish(detour_graph())
        options = self.options(data, detour=True)
        with patch("runmap.api.numeric_data.read_hill_intervals", side_effect=AssertionError("hill detail read")):
            first = generate_database_run(data, options)
        self.assertEqual(first.status, "complete")
        graphs = tuple(entry[0] for entry in data.numeric.graphs.values())
        high = options.model_copy(update={"preferences": PreferencesRequest(hills="high")})
        with patch("runmap.api.numeric_data.load_numeric_graph", side_effect=AssertionError("graph changed")), \
                patch("runmap.api.numeric_data.read_hill_intervals", wraps=read_hill_intervals) as reader:
            response = generate_database_run(data, high)
        self.assertEqual(response.status, "complete")
        self.assertEqual(tuple(entry[0] for entry in data.numeric.graphs.values()), graphs)
        self.assertEqual(reader.call_count, 2 * len(response.search.attempts))

    def test_endpoint_dispatch_validation_and_density_limits(self) -> None:
        data, _ = self.publish(detour_graph())
        options = self.options(data, detour=True)
        with patch.object(app.state, "paths", data, create=True), patch.object(
                app.state, "run_limits", RunAreaLimits(), create=True):
            request = Request({"type": "http", "app": app, "headers": []})
            result = run_route(request, options)
            self.assertEqual(result.status, "complete")
            self.assertEqual(RunResponse.model_validate_json(result.model_dump_json()), result)
            with self.assertRaises(HTTPException) as error:
                run_route(request, options.model_copy(update={"dataset_id": "stale"}))
            self.assertEqual(error.exception.status_code, 409)
            with self.assertRaises(HTTPException) as error:
                run_route(request, options.model_copy(update={"turnaround": options.home}))
            self.assertEqual(error.exception.status_code, 400)
        limited = generate_database_run(data, options, limits=RunAreaLimits(max_segments=1))
        self.assertEqual(limited.search.area_limited_reason, "maximum_segments")
        self.assertFalse(limited.candidates)
        missing = options.model_copy(update={"home": options.home.model_copy(update={"longitude": 20})})
        self.assertEqual(generate_database_run(data, missing).status, "no_eligible_snap")
        with patch("runmap.api.sqlite_runs.database_snap", side_effect=AssertionError("snapped")):
            with self.assertRaisesRegex(ValueError, "Unsupported signals"):
                generate_database_run(data, missing.model_copy(update={"preferences": PreferencesRequest(signals="high")}))

    def test_restrictions_preserve_reference_status_and_costs(self) -> None:
        for restriction in ("oneway", "stairs", "node"):
            graph = detour_graph()
            if restriction == "oneway":
                graph["segments"][-1]["tags"]["oneway:foot"] = "yes"
            elif restriction == "stairs":
                graph["segments"][-1]["tags"]["highway"] = "steps"
            else:
                graph["nodes"][1]["foot"] = "no"
            enrich(graph, lambda i, record: (.01, 90))
            data, source = self.publish(graph)
            for stairs in (True, False):
                options = self.options(data, detour=True).model_copy(update={"avoid_stairs": stairs})
                actual = generate_database_run(data, options)
                expected = generate_run(load_path_data(source), options)
                self.assertEqual(actual.status, expected.status)
                for a, b in zip(actual.candidates, expected.candidates):
                    self.assertAlmostEqual(a.legs[0].cost, b.legs[0].cost, places=4)
                    self.assertAlmostEqual(a.legs[1].cost, b.legs[1].cost, places=4)

    def test_deadline_retains_complete_candidates(self) -> None:
        data, _ = self.publish(detour_graph())
        def partial(graph: LoadedNumericGraph, home: NumericEndpoint, turnaround: NumericEndpoint,
                    home_point: Point, turnaround_point: Point, settings: RunSettings, *,
                    profile: EdgeProfile, preferences: PreferenceSettings, deadline: float,
                    mode: RunMode) -> NumericRunResult:
            result = numeric_plan_run(graph, home, turnaround, home_point, turnaround_point, settings,
                                      profile=profile, preferences=preferences, deadline=deadline)
            return replace(result, candidates=result.candidates[:1], outcomes=("complete", "deadline"))
        with patch("runmap.api.sqlite_runs.numeric_plan_run", side_effect=partial):
            result = generate_database_run(data, self.options(data, detour=True),
                                           limits=RunAreaLimits(minimum_dimension_m=5000))
        self.assertEqual(result.status, "deadline")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.search.attempts[-1].reason, "deadline")

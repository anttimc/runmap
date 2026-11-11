"""Local run planning preserves costs and expands detours without JSON loading."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from rasterio.warp import transform
from starlette.requests import Request

from runmap.api.app import app, run_route
from runmap.api.paths import load_path_data
from runmap.api.runs import PreferencesRequest, RunRequest, SettingsRequest
from runmap.routing_reference.api import generate_run
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import RunAreaLimits, generate_database_run, initial_run_bounds
from runmap.paths.database import export_network_database
from runmap.paths.graph import write_graph_outputs
from runmap.routing_reference.run_planner import plan_run
from runmap.routing.run_types import RunMode, RunSettings
from runmap.routing_reference.run_types import RunResult
from runmap.routing.graph import GraphData
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.hill_types import HillData
from runmap.routing.signals import SignalData
from runmap.paths.exploration import Snap
from runmap.routing.types import GraphDocument
from test_measurements import enrich
from test_path_api import fixture, get
from test_routing import graph_document


def detour_graph(*, connected: bool = True, bent_segment: bool = False) -> GraphDocument:
    points = [(375000, 6675000), (376000, 6675000), (375000, 6676200), (376000, 6676200)]
    lon, lat = transform("EPSG:3067", "EPSG:4326", *zip(*points))
    nodes = [(key, x, y) for key, x, y in zip(("a", "b", "u", "v"), lon, lat)]
    tags = {"highway": "path", "surface": "asphalt"}
    edges = [("au", "a", "u", 1200, 0, tags), ("vb", "v", "b", 1200, 0, tags)]
    if connected:
        edges.append(("uv", "u", "v", 1000, 0, tags))
    if bent_segment:
        edges = [("ab", "a", "b", 3400, 0, tags)]
    graph = cast(GraphDocument, graph_document(nodes, edges, start="a"))
    for segment in graph["segments"]:
        segment["osm_way_id"] = segment["id"]
    if bent_segment:
        graph["segments"][0]["geometry"] = [[lon[i], lat[i]] for i in (0, 2, 3, 1)]
    graph["summary"] = {}
    graph["experiment"] = {"bounds_epsg3067": [373000, 6673000, 378000, 6678000]}
    enrich(graph, lambda i, record: (.01, 90))
    return graph


class SQLiteRunTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def publish(self, graph: GraphDocument) -> tuple[SQLitePathData, Path]:
        root = self.root / str(len(list(self.root.iterdir())))
        source = write_graph_outputs(graph, root)["graph"]
        database = root / "network.sqlite"
        export_network_database(source, database)
        data = SQLitePathData(database)
        self.addCleanup(data.close)
        return data, source

    def options(self, data: SQLitePathData, *, detour: bool = False) -> RunRequest:
        if detour:
            lon, lat = transform("EPSG:3067", "EPSG:4326", [375000, 376000], [6675000, 6675000])
        else:
            lon, lat = [24.801, 24.803], [60.2, 60.2]
        return RunRequest(dataset_id=data.dataset_id, home={"longitude": lon[0], "latitude": lat[0]},
                          turnaround={"longitude": lon[1], "latitude": lat[1]})

    def test_one_way_succeeds_without_return_and_matches_reference(self) -> None:
        data, source = self.publish(fixture())
        reference = load_path_data(source)
        for level in ("off", "low", "high"):
            options = self.options(data).model_copy(update={"mode": "one_way",
                "preferences": PreferencesRequest(hills=level)})
            actual, expected = generate_database_run(data, options), generate_run(reference, options)
            self.assertEqual(actual.status, "complete")
            self.assertEqual(actual.mode, "one_way")
            self.assertEqual(actual.cost_model, expected.cost_model)
            self.assertEqual(actual.outcomes, ["complete"])
            self.assertEqual(len(actual.candidates), 1)
            self.assertEqual(len(actual.candidates[0].legs), 1)
            leg, expected_leg = actual.candidates[0].legs[0], expected.candidates[0].legs[0]
            self.assertAlmostEqual(leg.distance_m, 110, places=4)
            self.assertAlmostEqual(leg.cost, expected_leg.cost, places=4)
            self.assertEqual(leg.traversals, expected_leg.traversals)
            self.assertEqual(leg.coordinates, expected_leg.coordinates)
            self.assertEqual((leg.cost_breakdown.centre, leg.cost_breakdown.side, leg.cost_breakdown.reuse), (0, 0, 0))
            reverse = options.model_copy(update={"home": options.turnaround, "turnaround": options.home})
            self.assertEqual(generate_database_run(data, reverse).status, "unreachable")
            round_trip = options.model_copy(update={"mode": "round_trip"})
            self.assertEqual(generate_database_run(data, round_trip).status, "unreachable")
            missing = options.model_copy(update={"home": options.home.model_copy(update={"longitude": 20})})
            self.assertEqual(generate_database_run(data, missing).status, "no_eligible_snap")
            with self.assertRaises(ValueError):
                generate_database_run(data, options.model_copy(update={"turnaround": options.home}))

    def test_one_way_expands_detours_and_reports_area_and_deadline_limits(self) -> None:
        data, source = self.publish(detour_graph())
        options = self.options(data, detour=True).model_copy(update={"mode": "one_way"})
        actual = generate_database_run(data, options)
        expected = generate_run(load_path_data(source), options)
        self.assertEqual(actual.status, "complete")
        self.assertGreater(actual.search.retries, 0)
        self.assertAlmostEqual(actual.candidates[0].distance_m, expected.candidates[0].distance_m, places=4)
        limited = generate_database_run(data, options, limits=RunAreaLimits(max_retries=0))
        self.assertEqual(limited.status, "area_limited")
        self.assertFalse(limited.candidates)
        with patch("runmap.api.sqlite_runs.database_snap", side_effect=TimeoutError):
            self.assertEqual(generate_database_run(data, options).status, "deadline")

    def test_one_way_policies_and_reversed_partial_endpoints(self) -> None:
        graph = fixture()
        graph["segments"][0]["backward"] = True
        graph["segments"][0]["tags"]["highway"] = "steps"
        graph["segments"][0]["tags"]["surface"] = "dirt"
        graph["arcs"].append({"id": "s:b", "segment_id": "s", "from": "2", "to": "1", "length_m": 220})
        data, source = self.publish(graph)
        reference = load_path_data(source)
        options = self.options(data).model_copy(update={"mode": "one_way", "avoid_stairs": False})
        reverse = options.model_copy(update={"home": options.turnaround, "turnaround": options.home})
        for request in (options, reverse):
            actual = generate_database_run(data, request)
            expected = generate_run(reference, request)
            self.assertEqual(actual.status, "complete")
            self.assertEqual(actual.candidates[0].legs[0].traversals, expected.candidates[0].legs[0].traversals)
            self.assertEqual(actual.candidates[0].legs[0].coordinates, expected.candidates[0].legs[0].coordinates)
            for exclusion in ({"avoid_stairs": True}, {"surface": "surfaced"}):
                self.assertEqual(generate_database_run(data, request.model_copy(update=exclusion)).status, "no_eligible_snap")

    def test_costs_policies_partial_endpoints_and_hills_match_reference_without_sources(self) -> None:
        graph = fixture()
        graph["segments"][0]["backward"] = True
        graph["arcs"].append({"id": "s:b", "segment_id": "s", "from": "2", "to": "1", "length_m": 220})
        data, source = self.publish(graph)
        reference = load_path_data(source)
        with patch.object(Path, "read_text", side_effect=AssertionError("JSON read")), \
                patch("runmap.paths.database._query_measurements", side_effect=AssertionError("raw sample read")):
            for surface in ("all", "surfaced"):
                for stairs in (False, True):
                    for hills in ("off", "low", "high"):
                        for avoidance in (0, .18):
                            options = self.options(data).model_copy(update={"surface": surface, "avoid_stairs": stairs,
                                "preferences": PreferencesRequest(hills=hills), "settings": SettingsRequest(avoidance_ratio=avoidance)})
                            actual = generate_database_run(data, options)
                            expected = generate_run(reference, options)
                            self.assertEqual(actual.status, expected.status)
                            self.assertEqual(actual.candidates, expected.candidates)
                            self.assertEqual(actual.cost_model, expected.cost_model)
                            self.assertEqual(actual.search.retries, 0)
                            self.assertFalse(actual.search.area_limited_reason)
        self.assertFalse(hasattr(data, "routing_data"))

    def test_hills_off_does_not_prepare_hill_buffers(self) -> None:
        data, source = self.publish(fixture())
        options = self.options(data)
        expected = generate_run(load_path_data(source), options)
        with patch("runmap.paths.local_network._pack_hills", side_effect=AssertionError("unused hills")), \
                patch("runmap.paths.local_network.near_coverage_boundary", side_effect=AssertionError("whole-window coverage")):
            actual = generate_database_run(data, options)
        self.assertEqual(actual.status, expected.status)
        self.assertEqual(actual.candidates, expected.candidates)
        self.assertEqual(actual.cost_model, expected.cost_model)

    def test_detour_expands_all_candidates_under_one_deadline(self) -> None:
        data, source = self.publish(detour_graph())
        options = self.options(data, detour=True)
        reference = generate_run(load_path_data(source), options)
        with patch("runmap.routing_reference.sqlite_runs.plan_run", wraps=plan_run) as planner:
            result = generate_database_run(data, options)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.candidates, reference.candidates)
        self.assertEqual(result.search.retries, 1)
        self.assertEqual(len(result.search.attempts), 2)
        self.assertTrue(result.search.attempts[0].reached_frontier)
        self.assertEqual(result.search.attempts[-1].reason, "settled")
        self.assertEqual(len({call.args[4] for call in planner.call_args_list}), 1)
        self.assertFalse(result.search.attempts[-1].source_coverage_limited)

    def test_complete_segment_bending_outside_box_triggers_proximity_retry(self) -> None:
        data, source = self.publish(detour_graph(bent_segment=True))
        options = self.options(data, detour=True)
        actual = generate_database_run(data, options)
        self.assertEqual(actual.status, "complete")
        self.assertGreater(actual.search.retries, 0)
        self.assertTrue(actual.search.attempts[0].near_boundary)
        self.assertFalse(actual.search.attempts[0].reached_frontier)
        self.assertEqual(actual.search.attempts[0].reason, "near_boundary")
        self.assertEqual(actual.candidates, generate_run(load_path_data(source), options).candidates)

    def test_retry_area_and_density_limits_are_explicit_and_discard_partial_windows(self) -> None:
        data, _ = self.publish(detour_graph())
        options = self.options(data, detour=True)
        for limits, reason in ((RunAreaLimits(max_retries=0), "unloaded_connections"),
                               (RunAreaLimits(max_area_km2=.1), "maximum_area"),
                               (RunAreaLimits(max_segments=1), "maximum_segments")):
            result = generate_database_run(data, options, limits=limits)
            self.assertEqual(result.status, "area_limited")
            self.assertEqual(result.search.area_limited_reason, reason)
            self.assertFalse(result.candidates)
            self.assertEqual(result.outcomes, ["area_limited"])

    def test_unreachable_without_unloaded_connections_and_source_edges_are_distinct(self) -> None:
        graph = detour_graph(connected=False)
        graph["experiment"]["bounds_epsg3067"] = [374950, 6674900, 376050, 6676250]
        data, _ = self.publish(graph)
        result = generate_database_run(data, self.options(data, detour=True))
        self.assertEqual(result.status, "unreachable")
        self.assertEqual(result.search.retries, 0)
        self.assertFalse(result.search.area_limited_reason)
        self.assertTrue(result.search.attempts[0].source_coverage_limited)
        self.assertFalse(result.search.attempts[0].reached_frontier)

    def test_one_way_stairs_and_node_access_match_full_graph(self) -> None:
        for restriction in ("oneway", "stairs", "node"):
            graph = detour_graph()
            if restriction == "oneway":
                graph["segments"][-1]["tags"]["oneway:foot"] = "yes"
            elif restriction == "stairs":
                graph["segments"][-1]["tags"]["highway"] = "steps"
            else:
                graph["nodes"][1]["foot"] = "no"
            # The graph changed after sampling; publish fresh matching measurements.
            enrich(graph, lambda i, record: (.01, 90))
            data, source = self.publish(graph)
            reference = load_path_data(source)
            for stairs in (False, True):
                options = self.options(data, detour=True).model_copy(update={"avoid_stairs": stairs})
                actual = generate_database_run(data, options)
                expected = generate_run(reference, options)
                self.assertEqual(actual.status, expected.status)
                self.assertEqual(actual.candidates, expected.candidates)

    def test_deadline_covers_snapping_loading_and_retries_and_slots_are_released(self) -> None:
        data, _ = self.publish(detour_graph())
        options = self.options(data, detour=True)
        for operation in ("runmap.api.sqlite_runs.database_snap",
                          "runmap.routing_reference.sqlite_runs.load_local_network",
                          "runmap.routing_reference.sqlite_runs.plan_run"):
            with patch(operation, side_effect=TimeoutError):
                self.assertEqual(generate_database_run(data, options).status, "deadline")
        with patch("runmap.api.runs.RUN_SLOTS") as slots:
            slots.acquire.return_value = False
            with self.assertRaises(HTTPException) as error:
                generate_database_run(data, options)
            self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(generate_database_run(data, options).status, "complete")

    def test_no_snap_unsupported_preferences_and_identical_endpoints(self) -> None:
        data, _ = self.publish(detour_graph())
        options = self.options(data, detour=True)
        missing = options.model_copy(update={"home": options.home.model_copy(update={"longitude": 20})})
        self.assertEqual(generate_database_run(data, missing).status, "no_eligible_snap")
        unsupported = missing.model_copy(update={"preferences": PreferencesRequest(signals="high")})
        with patch("runmap.api.sqlite_runs.database_snap", side_effect=AssertionError("snapped")):
            with self.assertRaisesRegex(ValueError, "Unsupported signals"):
                generate_database_run(data, unsupported)
        with self.assertRaisesRegex(ValueError, "distinct points"):
            generate_database_run(data, options.model_copy(update={"turnaround": options.home}))

    def test_deadline_preserves_completed_candidates_from_one_window(self) -> None:
        graph = fixture()
        graph["segments"][0]["backward"] = True
        graph["arcs"].append({"id": "s:b", "segment_id": "s", "from": "2", "to": "1", "length_m": 220})
        data, _ = self.publish(graph)

        def partial(index: GraphData, home: Snap, turnaround: Snap, settings: RunSettings, deadline: float,
                      *, preferences: PreferenceSettings, hills: HillData | None,
                      frontier_nodes: frozenset[int], mode: RunMode, signals: SignalData | None = None) -> RunResult:
            result = plan_run(index, home, turnaround, settings, deadline, preferences=preferences,
                               hills=hills, frontier_nodes=frontier_nodes, signals=signals)
            return replace(result, candidates=result.candidates[:1], outcomes=("complete", "deadline"))

        with patch("runmap.routing_reference.sqlite_runs.plan_run", side_effect=partial):
            result = generate_database_run(data, self.options(data))
        self.assertEqual(result.status, "deadline")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.search.attempts[-1].reason, "deadline")

    def test_initial_box_and_limit_validation(self) -> None:
        self.assertEqual(initial_run_bounds([(0, 0), (3000, 4000)], 1000), (-2250, -1750, 5250, 5750))
        self.assertEqual(initial_run_bounds([(0, 0), (4000, 0)], 1000), (-1000, -3000, 5000, 3000))
        self.assertEqual(initial_run_bounds([(0, 0), (0, 4000)], 1000), (-3000, -1000, 3000, 5000))
        self.assertEqual(initial_run_bounds([(0, 0), (0, 1)], 1000), (-500, -499.5, 500, 500.5))
        data, _ = self.publish(detour_graph())
        for limits in (RunAreaLimits(max_segments=0), RunAreaLimits(max_area_km2=float("nan")),
                       RunAreaLimits(minimum_dimension_m=0), RunAreaLimits(max_retries=-1)):
            with self.assertRaises(ValueError):
                generate_database_run(data, self.options(data, detour=True), limits=limits)


class SQLiteRunEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_planning_is_available_without_json_and_reports_limits(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = write_graph_outputs(detour_graph(), root)["graph"]
            database = root / "network.sqlite"
            export_network_database(source, database)
            source.unlink()
            with closing(SQLitePathData(database)) as data, \
                    patch.object(Path, "read_text", side_effect=AssertionError("JSON read")):
                app.state.paths = data
                app.state.run_limits = RunAreaLimits()
                request = Request({"type": "http", "app": app, "headers": []})
                lon, lat = transform("EPSG:3067", "EPSG:4326", [375000, 376000], [6675000, 6675000])
                options = RunRequest(dataset_id=data.dataset_id, home={"longitude": lon[0], "latitude": lat[0]},
                                     turnaround={"longitude": lon[1], "latitude": lat[1]})
                self.assertTrue((await get("/api/paths/config"))[1]["run_planner"]["available"])
                self.assertEqual(run_route(request, options).status, "complete")
                app.state.run_limits = RunAreaLimits(max_retries=0)
                self.assertEqual(run_route(request, options).status, "area_limited")
                with self.assertRaises(HTTPException) as error:
                    run_route(request, options.model_copy(update={"dataset_id": "stale"}))
                self.assertEqual(error.exception.status_code, 409)

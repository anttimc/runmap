"""Native exploration preserves map semantics with graph-independent snapshots."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
from unittest.mock import patch

from rasterio.warp import transform

from runmap.api.paths import inspect_outing, inspect_path, load_path_data
from runmap.api.path_tiles import PathTileService
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_exploration import (ExplorationAreaError, ExplorationLimits, NumericExploration,
                                          prepare_local_exploration, retained_exploration_bytes)
from runmap.api.sqlite_paths import (database_inspect_outing, database_inspection,
                                    database_snap)
from runmap.paths.database import export_network_database
from runmap.paths.exploration import Snap
from runmap.paths.graph import write_graph_outputs
from runmap.paths.numeric_database import export_numeric_database
from runmap.routing.numeric_exploration import NumericDistanceData
from runmap.routing.numeric_search import LoadedNumericGraph, NumericEndpoint, NumericDistances, numeric_distances
from runmap.routing.types import EdgeProfile
from test_path_api import fixture
from test_path_database import publish_fixture
from test_sqlite_runs import detour_graph
from test_measurements import enrich
from runmap.api.app import app, SnapshotRequest, prepare_path_snapshot
from starlette.requests import Request
from test_path_api import get


class NumericExplorationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = write_graph_outputs(fixture(), self.root)["graph"]
        self.reference = load_path_data(self.source)
        self.database = self.root / "network.sqlite"
        export_network_database(self.source, self.database)
        export_numeric_database(self.database, self.root / "numeric-routing-v2.sqlite")
        self.data = SQLitePathData(self.database)
        self.addCleanup(self.data.close)

    def test_inspection_matches_reference_for_profiles_and_budgets(self) -> None:
        with patch.object(Path, "read_text", side_effect=AssertionError("JSON read")), \
                patch("runmap.api.sqlite_exploration.load_local_network", side_effect=AssertionError("Python graph")), \
                patch("runmap.api.sqlite_exploration.distance_data", side_effect=AssertionError("Python search")):
            service = PathTileService(self.data)
            for surface in ("all", "surfaced"):
                for stairs in (False, True):
                    for budget in (.03, .1, .3):
                        options = (24.801, 60.2, budget, surface, stairs, 50, None)
                        prepared = service.prepare(*options)
                        snapshot = service.snapshot(prepared["snapshot_id"])
                        self.assertIsInstance(snapshot.local, NumericExploration)
                        self.assertIsInstance(snapshot.distances, NumericDistanceData)
                        self.assertFalse(hasattr(snapshot.local, "routing"))
                        self.assertFalse(snapshot.distances.outward.values.flags.writeable)
                        for reverse in (False, True):
                            actual_inspection = database_inspect_outing(self.data,
                                database_inspection(self.data, "s", reverse, surface, stairs), prepared,
                                snapshot, budget, surface, stairs)
                            expected_inspection = inspect_outing(self.reference,
                                inspect_path(self.reference, "s", reverse, surface, stairs), *options[:3], surface, stairs)
                            self.assertEqual(actual_inspection, expected_inspection)

    def test_distant_segments_do_not_require_graph_membership(self) -> None:
        root = self.root / "chain"
        root.mkdir()
        source, _, _ = publish_fixture(root)
        reference = load_path_data(source)
        database = root / "network.sqlite"
        export_network_database(source, database)
        export_numeric_database(database, root / "numeric-routing-v2.sqlite")
        lon, lat = transform("EPSG:3067", "EPSG:4326", [375050], [6675000])
        with closing(SQLitePathData(database)) as data:
            service = PathTileService(data)
            options = (lon[0], lat[0], .03, "all", True, 50, None)
            prepared = service.prepare(*options)
            snapshot = service.snapshot(prepared["snapshot_id"])
            for key in ("bc", "de"):
                actual = database_inspect_outing(data, database_inspection(data, key), prepared, snapshot, .03, "all", True)
                expected = inspect_outing(reference, inspect_path(reference, key), *options)
                self.assertEqual(actual, expected)

    def test_frontier_expansion_and_area_segment_limits(self) -> None:
        root = self.root / "frontier"
        root.mkdir()
        source, _, _ = publish_fixture(root)
        database = root / "network.sqlite"
        with patch("runmap.paths.graph_chunks.MAX_CHUNK_SEGMENTS", 1):
            export_network_database(source, database)
        export_numeric_database(database, root / "numeric-routing-v2.sqlite")
        lon, lat = transform("EPSG:3067", "EPSG:4326", [375050], [6675000])
        with closing(SQLitePathData(database)) as data:
            snap = Snap(**database_snap(data, lon[0], lat[0], avoid_stairs=False)["snap"])
            tiny = (375041, 6674999, 375049, 6675001)
            with patch("runmap.api.sqlite_exploration.exploration_bounds", return_value=tiny):
                for limits, reason in ((ExplorationLimits(max_retries=0), "unloaded_connections"),
                                       (ExplorationLimits(max_segments=1, max_retries=5), "maximum_segments")):
                    with self.assertRaises(ExplorationAreaError) as error:
                        prepare_local_exploration(data, snap, .3, "all", False, monotonic()+5, limits)
                    self.assertEqual(error.exception.reason, reason)
                result = prepare_local_exploration(data, snap, .3, "all", False, monotonic()+5,
                                                   ExplorationLimits(max_retries=5))
                self.assertIsInstance(result, NumericExploration)
                self.assertGreater(result.retries, 0)
        with self.assertRaises(ExplorationAreaError) as error:
            PathTileService(self.data, exploration_limits=ExplorationLimits(max_area_km2=.001)).prepare(
                24.801, 60.2, .1, "all", True, 50, None)
        self.assertEqual(error.exception.reason, "maximum_area")

    def test_snapshots_coalesce_and_survive_graph_cache_eviction_and_shutdown(self) -> None:
        from runmap.api.numeric_data import load_numeric_graph
        service = PathTileService(self.data)
        with patch("runmap.api.numeric_data.load_numeric_graph", wraps=load_numeric_graph) as loader, \
                patch("runmap.api.numeric_data.read_hill_intervals", side_effect=AssertionError("exploration hill detail")), \
                ThreadPoolExecutor(max_workers=8) as workers:
            prepared = list(workers.map(lambda _: service.prepare(24.801, 60.2, .1, "all", True, 50, None), range(16)))
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(len({p["snapshot_id"] for p in prepared}), 1)
        snapshot = service.snapshot(prepared[0]["snapshot_id"])
        self.assertGreaterEqual(snapshot.retained_bytes, retained_exploration_bytes(snapshot.local))
        expected = database_inspect_outing(self.data, database_inspection(self.data, "s"), prepared[0], snapshot, .1, "all", True)
        with self.assertRaisesRegex(ValueError, "memory budget"):
            PathTileService(self.data, snapshot_bytes=1).prepare(24.801, 60.2, .1, "all", True, 50, None)
        second = service.prepare(24.802, 60.2, .1, "all", True, 50, None)
        capacity = max(snapshot.retained_bytes, service.snapshot(second["snapshot_id"]).retained_bytes) + 128
        small = PathTileService(self.data, snapshot_bytes=capacity)
        one = small.prepare(24.801, 60.2, .1, "all", True, 50, None)
        two = small.prepare(24.802, 60.2, .1, "all", True, 50, None)
        with self.assertRaises(KeyError):
            small.snapshot(one["snapshot_id"])
        self.assertIsInstance(small.snapshot(two["snapshot_id"]).local, NumericExploration)
        self.assertLessEqual(small.snapshots.bytes, capacity)
        # Drop graph owners while retaining independent sparse distance bytes.
        self.data.numeric.close()
        self.data.numeric = None
        self.assertEqual(database_inspect_outing(self.data, database_inspection(self.data, "s"), prepared[0], snapshot, .1, "all", True), expected)
        with patch("runmap.api.path_tiles.monotonic", return_value=monotonic()+1000):
            with self.assertRaises(KeyError):
                service.snapshot(snapshot.id)
        self.assertEqual(service.snapshots.bytes, 0)

    def test_partial_starts_and_access_restrictions_match_reference(self) -> None:
        for restriction in ("bidirectional", "oneway", "stairs", "node"):
            root = self.root / restriction
            root.mkdir()
            graph = detour_graph()
            if restriction == "oneway":
                graph["segments"][-1]["tags"]["oneway:foot"] = "yes"
            elif restriction == "stairs":
                graph["segments"][-1]["tags"]["highway"] = "steps"
            elif restriction == "node":
                graph["nodes"][1]["foot"] = "no"
            enrich(graph, lambda i, record: (.01, 90))
            source = write_graph_outputs(graph, root)["graph"]
            reference = load_path_data(source)
            database = root / "network.sqlite"
            with patch("runmap.paths.graph_chunks.MAX_CHUNK_SEGMENTS", 1):
                export_network_database(source, database)
            export_numeric_database(database, root / "numeric-routing-v2.sqlite")
            lon, lat = transform("EPSG:3067", "EPSG:4326", [375000], [6675300])
            with closing(SQLitePathData(database)) as data:
                service = PathTileService(data)
                for stairs in (True, False):
                    for budget in (.15, 1.5, 4):
                        options = (lon[0], lat[0], budget, "all", stairs, 50, None)
                        prepared = service.prepare(*options)
                        snapshot = service.snapshot(prepared["snapshot_id"])
                        for key in reference.segments:
                            actual = database_inspect_outing(data, database_inspection(data, key, avoid_stairs=stairs),
                                prepared, snapshot, budget, "all", stairs)
                            expected = inspect_outing(reference, inspect_path(reference, key, avoid_stairs=stairs), *options)
                            self.assertEqual(actual, expected)

    def test_shared_cache_and_deadlines(self) -> None:
        service = PathTileService(self.data)
        def observed(graph: LoadedNumericGraph, start: NumericEndpoint, maximum_m: float, *,
                     profile: EdgeProfile, deadline: float) -> NumericDistances:
            self.assertEqual(self.data.pool.connections.qsize(), 1)
            return numeric_distances(graph, start, maximum_m, profile=profile, deadline=deadline)

        with patch("runmap.api.sqlite_exploration.numeric_distances", side_effect=observed):
            first = service.prepare(24.801, 60.2, .1, "all", True, 50, None)
        local = service.snapshot(first["snapshot_id"]).local
        snap = service.snapshot(first["snapshot_id"]).distances.snap
        with patch("runmap.api.numeric_data.load_numeric_graph", side_effect=AssertionError("cache miss")):
            graph, home, end = self.data.numeric.prepare(local.bounds, (snap, snap), monotonic()+5)
            self.assertEqual(home, end)
            self.assertEqual(graph.native.node_count, local.nodes)
        with patch("runmap.api.sqlite_exploration.numeric_distances", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                PathTileService(self.data).prepare(24.801, 60.2, .1, "all", True, 50, None)
        self.assertEqual(self.data.pool.connections.qsize(), 2)
        with self.data.pool.connection(), self.data.pool.connection():
            with self.assertRaises(TimeoutError):
                PathTileService(self.data, preparation_deadline=.01).prepare(24.801, 60.2, .1, "all", True, 50, None)
        self.assertEqual(service.prepare(20, 60.2, .1, "all", True, 50, None)["status"], "no_eligible_snap")
        for budget in (0, float("nan"), 51):
            with self.assertRaises(ValueError):
                service.prepare(24.801, 60.2, budget, "all", True, 50, None)


class NumericExplorationEndpointTests(unittest.IsolatedAsyncioTestCase):
    setUp = NumericExplorationTests.setUp

    async def test_endpoints_use_native_search_and_preserve_error_responses(self) -> None:
        service = PathTileService(self.data)
        with patch.object(app.state, "paths", self.data, create=True), \
                patch.object(app.state, "path_tiles", service, create=True), \
                patch("runmap.api.sqlite_exploration.load_local_network", side_effect=AssertionError("Python graph")):
            query = "west=24.799&south=60.199&east=24.805&north=60.201&zoom=15&longitude=24.801&latitude=60.2&budget_km=.1"
            self.assertEqual((await get("/api/paths/segments/s?"+query))[0], 200)
            self.assertEqual((await get("/api/paths/segments/s?"+query+"&dataset_id=stale"))[0], 409)
            request = Request({"type": "http", "app": app, "headers": []})
            options = SnapshotRequest(longitude=24.801, latitude=60.2, budget_km=.1, dataset_id=self.data.dataset_id)
            self.assertEqual(prepare_path_snapshot(request, options).status_code, 200)
            app.state.path_tiles = PathTileService(self.data, exploration_limits=ExplorationLimits(max_area_km2=.001))
            status, result = await get("/api/paths/segments/s?"+query)
            self.assertEqual(status, 422)
            self.assertEqual(result["status"], "area_limited")
            app.state.path_tiles = PathTileService(self.data, preparation_deadline=-1)
            self.assertEqual((await get("/api/paths/segments/s?"+query))[0], 504)

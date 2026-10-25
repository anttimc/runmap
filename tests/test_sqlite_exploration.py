"""Local exploration preserves inclusion semantics and bounds request ownership."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
from unittest.mock import patch

from rasterio.warp import transform
from starlette.requests import Request

from runmap.api.app import app, prepare_path_snapshot, SnapshotRequest
from runmap.api.paths import inspect_outing, inspect_path, load_path_data
from runmap.api.path_tiles import PathTileService
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_exploration import (ExplorationAreaError, ExplorationLimits, exploration_bounds,
                                          prepare_local_exploration, retained_exploration_bytes)
from runmap.api.sqlite_paths import (database_inspect_outing, database_inspection,
                                    database_snap)
from runmap.paths.database import export_network_database
from runmap.paths.exploration import Snap, interval_inclusion
from runmap.paths.graph import write_graph_outputs
from test_path_api import fixture, get
from test_path_database import publish_fixture




class SQLiteExplorationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = write_graph_outputs(fixture(), self.root)["graph"]
        self.reference = load_path_data(self.source)
        self.database = self.root / "network.sqlite"
        export_network_database(self.source, self.database)
        self.data = SQLitePathData(self.database)
        self.addCleanup(self.data.close)

    def test_inspection_matches_reference_without_json(self) -> None:
        with patch.object(Path, "read_text", side_effect=AssertionError("JSON read")), \
                patch("runmap.api.paths.load_path_data", side_effect=AssertionError("legacy bridge")):
            service = PathTileService(self.data)
            for surface in ("all", "surfaced"):
                for stairs in (False, True):
                    for budget in (.03, .1, .3):
                        options = (24.801, 60.2, budget, surface, stairs, 50, None)
                        prepared = service.prepare(*options)
                        snapshot = service.snapshot(prepared["snapshot_id"])
                        self.assertIs(snapshot.distances.outward.node_ids, snapshot.local.routing.integer.node_ids)
                        self.assertGreater(snapshot.retained_bytes, snapshot.distances.outward.values.nbytes)
                        for reverse in (False, True):
                            result = database_inspection(self.data, "s", reverse, surface, stairs)
                            actual_inspection = database_inspect_outing(self.data, result, prepared, snapshot,
                                                                         budget, surface, stairs)
                            expected_inspection = inspect_outing(self.reference,
                                inspect_path(self.reference, "s", reverse, surface, stairs), *options[:3], surface, stairs)
                            self.assertEqual(actual_inspection, expected_inspection)
            self.assertFalse(hasattr(self.data, "legacy"))

    def test_distant_segments_need_no_local_graph_membership(self) -> None:
        root = self.root / "chain"
        root.mkdir()
        source, _, _ = publish_fixture(root)
        reference = load_path_data(source)
        database = root / "network.sqlite"
        export_network_database(source, database)
        lon, lat = transform("EPSG:3067", "EPSG:4326", [375050], [6675000])
        with closing(SQLitePathData(database)) as data:
            service = PathTileService(data)
            options = (lon[0], lat[0], .03, "all", True, 50, None)
            prepared = service.prepare(*options)
            snapshot = service.snapshot(prepared["snapshot_id"])
            self.assertNotIn("de", snapshot.local.routing.segments)
            self.assertIsNone(snapshot.local.routing.document.get("measurements"))
            for key in ("bc", "de"):
                actual = database_inspect_outing(data, database_inspection(data, key), prepared, snapshot, .03, "all", True)
                wanted = inspect_outing(reference, inspect_path(reference, key), *options)
                self.assertEqual(actual, wanted)

    def test_no_snap_validation_deadline_and_configured_limits(self) -> None:
        service = PathTileService(self.data)
        self.assertEqual(service.prepare(20, 60.2, .1, "all", True, 50, None)["status"], "no_eligible_snap")
        self.assertFalse(service.snapshots.entries)
        for budget in (0, float("nan"), 51):
            with self.assertRaises(ValueError):
                service.prepare(24.801, 60.2, budget, "all", True, 50, None)
        with self.assertRaises(TimeoutError):
            PathTileService(self.data, preparation_deadline=-1).prepare(24.801, 60.2, .1, "all", True, 50, None)
        for limits, reason in ((ExplorationLimits(max_area_km2=.001), "maximum_area"),
                               (ExplorationLimits(max_segments=0), None)):
            if reason is None:
                with self.assertRaises(ValueError):
                    PathTileService(self.data, exploration_limits=limits)
            else:
                with self.assertRaises(ExplorationAreaError) as error:
                    PathTileService(self.data, exploration_limits=limits).prepare(24.801, 60.2, .1, "all", True, 50, None)
                self.assertEqual(error.exception.reason, reason)

    def test_reached_frontier_retries_or_reports_area_limit(self) -> None:
        root = self.root / "frontier"
        root.mkdir()
        source, _, _ = publish_fixture(root)
        database = root / "network.sqlite"
        with patch("runmap.paths.graph_chunks.MAX_CHUNK_SEGMENTS", 1):
            export_network_database(source, database)
        lon, lat = transform("EPSG:3067", "EPSG:4326", [375050], [6675000])
        with closing(SQLitePathData(database)) as data:
            with data.pool.connection() as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM graph_chunks").fetchone()[0], 3)
            start = database_snap(data, lon[0], lat[0], avoid_stairs=False)
            snap = Snap(**start["snap"])
            # Deliberately undersize the initial envelope to exercise the safety net.
            tiny = (375041, 6674999, 375049, 6675001)
            with patch("runmap.api.sqlite_exploration.exploration_bounds", return_value=tiny):
                with self.assertRaises(ExplorationAreaError) as error:
                    prepare_local_exploration(data, snap, .3, "all", False, monotonic() + 5,
                                               ExplorationLimits(max_retries=0))
                self.assertEqual(error.exception.reason, "unloaded_connections")
                result = prepare_local_exploration(data, snap, .3, "all", False, monotonic() + 5,
                                                     ExplorationLimits(max_retries=5))
                self.assertGreater(result.retries, 0)
                self.assertIn("bc", result.routing.segments)
                with self.assertRaises(ExplorationAreaError) as error:
                    prepare_local_exploration(data, snap, .3, "all", False, monotonic() + 5,
                                               ExplorationLimits(max_segments=1, max_retries=5))
                self.assertEqual(error.exception.reason, "maximum_segments")

    def test_snapshot_cache_coalesces_graph_loading_and_charges_graph_ownership(self) -> None:
        from runmap.api.sqlite_exploration import load_local_network
        service = PathTileService(self.data)
        with patch("runmap.api.sqlite_exploration.load_local_network", wraps=load_local_network) as loader, \
                patch("runmap.paths.database._query_measurements", side_effect=AssertionError("loaded local samples")), \
                patch("runmap.paths.local_network._pack_hills", side_effect=AssertionError("loaded local hills")), \
                ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: service.prepare(24.801, 60.2, .1, "all", True, 50, None), range(16)))
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(len({result["snapshot_id"] for result in results}), 1)
        first = service.snapshot(results[0]["snapshot_id"])
        self.assertGreaterEqual(first.retained_bytes, retained_exploration_bytes(first.local))
        small = PathTileService(self.data, snapshot_bytes=first.retained_bytes + 1)
        one = small.prepare(24.801, 60.2, .1, "all", True, 50, None)
        two = small.prepare(24.802, 60.2, .1, "all", True, 50, None)
        self.assertLessEqual(small.snapshots.bytes, small.snapshots.capacity)
        with self.assertRaises(KeyError):
            small.snapshot(one["snapshot_id"])
        later = small.snapshot(two["snapshot_id"])
        with self.assertRaisesRegex(ValueError, "different prepared integer graph"):
            interval_inclusion(later.local.routing, first.distances, "s", 0, 55, 100)
        with self.assertRaisesRegex(ValueError, "memory budget"):
            PathTileService(self.data, snapshot_bytes=1).prepare(24.801, 60.2, .1, "all", True, 50, None)
        with patch("runmap.api.path_tiles.monotonic", return_value=monotonic() + 1000):
            with self.assertRaises(KeyError):
                service.snapshot(first.id)
        self.assertEqual(service.snapshots.bytes, 0)

    def test_envelope_protects_long_bent_start_and_distance_scale(self) -> None:
        self.assertEqual(exploration_bounds([(0, 0), (5000, 9000), (1, 0)], 100, .5),
                         (-201, -201, 5201, 9201))
        for scale in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                exploration_bounds([(0, 0)], 100, scale)

    def test_pool_wait_and_sql_work_respect_deadlines_and_release_connections(self) -> None:
        with self.data.pool.connection(), self.data.pool.connection():
            with self.assertRaises(TimeoutError):
                PathTileService(self.data, preparation_deadline=.01).prepare(
                    24.801, 60.2, .1, "all", True, 50, None)
        with self.assertRaises(TimeoutError):
            with self.data.pool.connection(deadline=monotonic() + .01) as connection:
                connection.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10000000) SELECT sum(x) FROM n").fetchone()
        with self.data.pool.connection() as connection:
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(self.data.pool.connections.qsize(), 2)


class SQLiteExplorationEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoints_use_database_only_and_report_limits(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = write_graph_outputs(fixture(), root)["graph"]
            database = root / "network.sqlite"
            export_network_database(source, database)
            with closing(SQLitePathData(database)) as data, \
                    patch.object(Path, "read_text", side_effect=AssertionError("source read")):
                app.state.paths = data
                app.state.path_tiles = PathTileService(data)
                query = "west=24.799&south=60.199&east=24.805&north=60.201&zoom=15&longitude=24.801&latitude=60.2&budget_km=.1"
                self.assertEqual((await get("/api/paths/segments/s?" + query))[0], 200)
                self.assertEqual((await get("/api/paths/segments/s?" + query + "&dataset_id=stale"))[0], 409)
                request = Request({"type": "http", "app": app, "headers": []})
                options = SnapshotRequest(longitude=24.801, latitude=60.2, budget_km=.1, dataset_id=data.dataset_id)
                self.assertEqual(prepare_path_snapshot(request, options).status_code, 200)
                app.state.path_tiles = PathTileService(data, exploration_limits=ExplorationLimits(max_area_km2=.001))
                status, result = await get("/api/paths/segments/s?" + query)
                self.assertEqual(status, 422)
                self.assertEqual(result["status"], "area_limited")
                app.state.path_tiles = PathTileService(data, preparation_deadline=-1)
                self.assertEqual((await get("/api/paths/segments/s?" + query))[0], 504)
                self.assertFalse(hasattr(data, "legacy"))

"""Tile rendering must not monopolize the shared database leases."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, local
import unittest
from unittest.mock import patch

from rasterio.warp import transform_bounds

from runmap.api import sqlite_paths
from runmap.api.path_tiles import DisplayRun, PathTileService, Property
from runmap.api.sqlite_data import SQLitePathData
from runmap.paths.database import export_network_database
from runmap.paths.graph import write_graph_outputs
from runmap.paths.numeric_database import export_numeric_database
from runmap.paths.preparation import PreparedDisplayInterval
from runmap.routing.profiles import exploration_profile
from test_path_api import fixture


VIEW = (24.799, 60.199, 24.805, 60.201)


class SQLiteTileConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = write_graph_outputs(fixture(), root)["graph"]
        database = root / "network.sqlite"
        export_network_database(source, database)
        export_numeric_database(database, root / "numeric-routing-v2.sqlite")
        self.data = SQLitePathData(database, pool_size=1)
        self.addCleanup(self.data.close)

    def test_parallel_static_and_snapshot_tiles_release_leases_before_processing(self) -> None:
        service = PathTileService(self.data)
        prepared = service.prepare(24.801, 60.2, .1, "all", True, 50, None)
        snapshot = service.snapshot(prepared["snapshot_id"])
        self.data.pool.timeout_s = .2
        for context in (None, snapshot):
            expected = sqlite_paths.database_tile_runs(self.data, VIEW, "all-no-stairs", "distance-all", 12, context)
            barrier, threads = Barrier(5), local()
            original = sqlite_paths._tile_properties

            def slow_properties(stored: PreparedDisplayInterval, metric: str, z: int) -> dict[str, Property]:
                if not getattr(threads, "rendering", False):
                    threads.rendering = True
                    # All workers must reach processing using the single shared connection.
                    barrier.wait(timeout=3)
                return original(stored, metric, z)

            def render(_: int) -> list[DisplayRun]:
                return sqlite_paths.database_tile_runs(self.data, VIEW, "all-no-stairs", "distance-all", 12, context)

            with patch.object(sqlite_paths, "_tile_properties", side_effect=slow_properties), \
                    ThreadPoolExecutor(max_workers=5) as workers:
                results = list(workers.map(render, range(5)))
            self.assertTrue(all(result == expected for result in results))
            self.assertEqual(self.data.pool.connections.qsize(), 1)

    def test_interval_batches_are_bounded_ordered_and_yield_without_a_lease(self) -> None:
        bounds = transform_bounds("EPSG:4326", "EPSG:3067", *VIEW)
        original = sqlite_paths.query_display_interval_batch
        with patch.object(sqlite_paths, "TILE_INTERVAL_BATCH_SIZE", 1), \
                patch.object(sqlite_paths, "query_display_interval_batch", wraps=original) as fetch:
            intervals = sqlite_paths._tile_intervals(self.data, bounds, exploration_profile("all", True)["id"], False, True)
            actual = []
            for interval in intervals:
                self.assertIsNotNone(interval.endpoints)
                self.assertEqual(self.data.pool.connections.qsize(), 1)
                with self.data.pool.connection(timeout_s=.01) as connection:
                    self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
                actual.append(interval.measurement["interval_index"])
            self.assertEqual(actual, [0, 1, 2, 3])
            self.assertEqual(fetch.call_count, 4)
            self.assertTrue(all(len(call.args[1]) == 1 for call in fetch.call_args_list))

    def test_snapshot_tile_uses_precomputed_coverage_flags(self) -> None:
        service = PathTileService(self.data)
        prepared = service.prepare(24.801, 60.2, .1, "all", True, 50, None)
        snapshot = service.snapshot(prepared["snapshot_id"])
        expected = sqlite_paths.database_tile_runs(self.data, VIEW, snapshot.policy, "distance-all", 12, snapshot)
        with patch.object(sqlite_paths, "near_coverage_boundary", side_effect=AssertionError("coverage recomputed")):
            actual = sqlite_paths.database_tile_runs(self.data, VIEW, snapshot.policy, "distance-all", 12, snapshot)
        self.assertEqual(actual, expected)

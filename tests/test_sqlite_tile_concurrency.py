"""Gradient tile preparation is bounded and independent of distance state."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, local
import unittest
from unittest.mock import patch

import mapbox_vector_tile
from rasterio.warp import transform_bounds

from runmap.api import sqlite_paths
from runmap.api.path_tiles import DisplayRun, PathTileService, Property, encode_tile
from runmap.api.sqlite_data import SQLitePathData
from runmap.paths.database import export_network_database
from runmap.paths.graph import write_graph_outputs
from runmap.paths.numeric_database import export_numeric_database
from runmap.paths.tile_inputs import TileInputs
from runmap.routing.profiles import exploration_profile
from test_path_api import fixture
from test_path_tiles import xyz


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

    def test_parallel_gradient_tiles_release_leases_before_processing(self) -> None:
        self.data.pool.timeout_s = .2
        expected = sqlite_paths.database_tile_runs(self.data, VIEW, "all-no-stairs", "gradient-all", 12)
        barrier, threads = Barrier(5), local()
        original = sqlite_paths._packed_properties

        def slow_properties(inputs: TileInputs, i: int, z: int) -> dict[str, Property]:
            if not getattr(threads, "rendering", False):
                threads.rendering = True
                barrier.wait(timeout=3)
            return original(inputs, i, z)

        def render(_: int) -> list[DisplayRun]:
            return sqlite_paths.database_tile_runs(self.data, VIEW, "all-no-stairs", "gradient-all", 12)

        with patch.object(sqlite_paths, "_packed_properties", side_effect=slow_properties), \
                ThreadPoolExecutor(max_workers=5) as workers:
            results = list(workers.map(render, range(5)))
        self.assertTrue(all(result == expected for result in results))
        self.assertEqual(self.data.pool.connections.qsize(), 1)

    def test_interval_batches_are_bounded_and_skip_distance_endpoints(self) -> None:
        bounds = transform_bounds("EPSG:4326", "EPSG:3067", *VIEW)
        original = sqlite_paths.query_display_interval_batch
        with patch.object(sqlite_paths, "TILE_INTERVAL_BATCH_SIZE", 1), \
                patch.object(sqlite_paths, "query_display_interval_batch", wraps=original) as fetch:
            intervals = sqlite_paths._tile_intervals(self.data, bounds, exploration_profile("all", True)["id"], False)
            actual = []
            for interval in intervals:
                self.assertFalse(hasattr(interval, "endpoints"))
                self.assertEqual(self.data.pool.connections.qsize(), 1)
                with self.data.pool.connection(timeout_s=.01) as connection:
                    self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
                actual.append(interval.measurement["interval_index"])
            self.assertEqual(actual, [0, 1, 2, 3])
            self.assertEqual(fetch.call_count, 4)
            self.assertTrue(all(len(call.args[1]) == 1 for call in fetch.call_args_list))

    def test_gradient_tile_needs_no_inspection_distance_or_coverage_work(self) -> None:
        expected = sqlite_paths.database_tile_runs(self.data, VIEW, "all-no-stairs", "gradient-all", 12)
        with patch.object(sqlite_paths, "near_coverage_boundary", side_effect=AssertionError("coverage recomputed")), \
                patch.object(sqlite_paths, "numeric_segment_distances", side_effect=AssertionError("distance computed")):
            actual = sqlite_paths.database_tile_runs(self.data, VIEW, "all-no-stairs", "gradient-all", 12)
        self.assertEqual(actual, expected)

    def test_gradient_inputs_coalesce_without_retaining_distance_state(self) -> None:
        service = PathTileService(self.data)
        expected = sqlite_paths.database_tile_runs(self.data, VIEW, "all-no-stairs", "gradient-all", 12)

        def render(_: int) -> list[DisplayRun]:
            return sqlite_paths.database_tile_runs(self.data, VIEW, "all-no-stairs", "gradient-all", 12,
                                                   input_cache=service.tile_inputs)

        with patch.object(sqlite_paths, "_tile_intervals", wraps=sqlite_paths._tile_intervals) as reader, \
                ThreadPoolExecutor(max_workers=2) as workers:
            actual = list(workers.map(render, range(2)))
        self.assertEqual(reader.call_count, 1)
        self.assertTrue(all(result == expected for result in actual))
        self.assertLessEqual(service.tile_inputs.bytes, service.tile_inputs.capacity)
        inputs = next(iter(service.tile_inputs.entries.values())).value
        self.assertTrue(all(view.readonly for view in (inputs.indices, inputs.values, inputs.flags,
                                                      inputs.geometry_offsets, inputs.geometry)))
        self.assertTrue(all(not hasattr(segment, "endpoints") for segment in inputs.segments))
        self.assertEqual(len(inputs.values), len(inputs.indices))
        self.assertFalse(hasattr(inputs, "distances"))

    def test_oversized_tile_inputs_render_without_retention(self) -> None:
        service = PathTileService(self.data, tile_input_bytes=1)
        x, y = xyz(24.801, 60.2, 12)
        actual, _, _ = service.tile("all-no-stairs", "gradient-hidden", 12, x, y)
        expected = encode_tile(self.data, "all-no-stairs", "gradient-hidden", 12, x, y)
        self.assertEqual(mapbox_vector_tile.decode(actual), mapbox_vector_tile.decode(expected))
        self.assertEqual(service.tile_inputs.bytes, 0)
        self.assertFalse(service.tile_inputs.entries)

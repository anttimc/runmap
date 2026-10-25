"""SQLite delivery preserves API semantics without eagerly loading routing data."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import mapbox_vector_tile
from rasterio.warp import transform, transform_bounds
from starlette.requests import Request

from runmap.api.app import app, lifespan, static_path_tile
from runmap.api.paths import inspect_path, load_path_data, path_config, snap_path_start, viewport_paths
from runmap.api.path_tiles import PathTileService, encode_tile, render_version
from runmap.api.runs import RunRequest, generate_run
from runmap.api.sqlite_runs import generate_database_run
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_paths import database_inspection, database_path_config, database_snap, database_viewport
from runmap.paths.database import export_network_database
from runmap.paths.database_pool import DatabaseBusyError, DatabasePool
from runmap.paths.graph import write_graph_outputs
from test_path_api import fixture, get
from test_path_tiles import xyz
from test_database_features import driveway_graph
from test_measurements import enrich


VIEW = (24.799, 60.199, 24.805, 60.201)


class SQLiteDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        graph = fixture()
        graph["segments"][0]["backward"] = True
        graph["arcs"].append({"id": "s:b", "segment_id": "s", "from": "2", "to": "1", "length_m": 220})
        self.graph = write_graph_outputs(graph, self.root)["graph"]
        self.reference = load_path_data(self.graph)
        self.database = self.root / "network.sqlite"
        export_network_database(self.graph, self.database)
        self.data = SQLitePathData(self.database)
        self.addCleanup(self.data.close)

    def test_config_inspection_viewport_and_snapping_match_reference(self) -> None:
        self.assertEqual(database_path_config(self.data), path_config(self.reference))
        for surface in ("all", "surfaced"):
            for stairs in (False, True):
                for reverse in (False, True):
                    self.assertEqual(database_inspection(self.data, "s", reverse, surface, stairs),
                                     inspect_path(self.reference, "s", reverse, surface, stairs))
                for bounds, zoom, limit in ((VIEW, 15, 500), (VIEW, 9, 500), (VIEW, 15, 0),
                                           ((25, 61, 26, 62), 15, 500)):
                    self.assertEqual(database_viewport(self.data, bounds, zoom, surface, stairs, max_features=limit),
                                     viewport_paths(self.reference, bounds, zoom, surface, limit, stairs))
                for longitude, preferred in ((24.801, None), (24.802, "s"), (24.802, "missing"), (20, None)):
                    self.assertEqual(database_snap(self.data, longitude, 60.2, surface, stairs, 50, preferred),
                                     snap_path_start(self.reference, longitude, 60.2, surface, stairs, 50, preferred))
        self.assertFalse(hasattr(self.data, "legacy"))

    def test_static_tiles_preserve_decoded_geometry_properties_and_cache(self) -> None:
        with patch("runmap.api.paths.load_path_data", side_effect=AssertionError("loaded routing")):
            for z in (10, 12, 14, 16):
                x, y = xyz(24.801, 60.2, z)
                for policy in ("all-stairs", "all-no-stairs", "surfaced-stairs", "surfaced-no-stairs"):
                    for variant in ("gradient-hidden", "gradient-all"):
                        self.assertEqual(mapbox_vector_tile.decode(encode_tile(self.data, policy, variant, z, x, y)),
                                         mapbox_vector_tile.decode(encode_tile(self.reference, policy, variant, z, x, y)))
        service = PathTileService(self.data, tile_bytes=100000)
        x, y = xyz(24.801, 60.2, 15)
        self.assertFalse(service.tile("all-no-stairs", "gradient-hidden", 15, x, y)[1])
        self.assertTrue(service.tile("all-no-stairs", "gradient-hidden", 15, x, y)[1])
        self.assertLessEqual(service.tiles.bytes, 100000)
        self.assertNotEqual(render_version(self.data), render_version(self.reference))

    def test_delivery_and_startup_do_not_read_source_or_rebuild_graph(self) -> None:
        with patch.object(Path, "read_text", side_effect=AssertionError("source read")), \
                patch("runmap.api.paths.load_path_data", side_effect=AssertionError("loaded graph")):
            with closing(SQLitePathData(self.database)) as data:
                self.assertEqual(database_path_config(data)["status"], "ready")
                self.assertTrue(database_viewport(data, VIEW, 15)["features"])
                self.assertTrue(database_inspection(data, "s")["intervals"])
                self.assertEqual(database_snap(data, 24.801, 60.2)["status"], "ready")
                self.assertFalse(hasattr(data, "legacy"))

    def test_startup_sql_reads_only_metadata(self) -> None:
        statements: list[str] = []
        connect = sqlite3.connect

        def traced(database: str, *, uri: bool = False, check_same_thread: bool = True) -> sqlite3.Connection:
            connection = connect(database, uri=uri, check_same_thread=check_same_thread)
            connection.set_trace_callback(statements.append)
            return connection

        with patch("runmap.paths.database.sqlite3.connect", side_effect=traced), \
                closing(SQLitePathData(self.database)):
            pass
        selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
        self.assertTrue(selects)
        self.assertTrue(all("FROM metadata" in statement for statement in selects))

    def test_planning_and_snapshots_need_no_json_bridge(self) -> None:
        with patch.object(Path, "read_text", side_effect=AssertionError("JSON read")):
            options = RunRequest(dataset_id=self.data.dataset_id, home={"longitude": 24.801, "latitude": 60.2},
                                 turnaround={"longitude": 24.803, "latitude": 60.2})
            self.assertEqual(generate_database_run(self.data, options).candidates,
                             generate_run(self.reference, options).candidates)
            service = PathTileService(self.data)
            result = service.prepare(24.801, 60.2, .1, "all", True, 50, None)
            snapshot = service.snapshot(result["snapshot_id"])
            x, y = xyz(24.801, 60.2, 15)
            actual = service.tile(snapshot.policy, "gradient-all", 15, x, y)[0]
            expected = encode_tile(self.reference, snapshot.policy, "gradient-all", 15, x, y)
            self.assertEqual(mapbox_vector_tile.decode(actual), mapbox_vector_tile.decode(expected))
        self.assertFalse(hasattr(self.data, "routing_data"))

    def test_pool_bounds_concurrent_readers_and_closes_returned_connections(self) -> None:
        def inspect(_: int) -> str:
            return database_inspection(self.data, "s")["segment_id"]
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(list(executor.map(inspect, range(24))), ["s"] * 24)
        self.assertEqual(self.data.pool.connections.qsize(), 2)
        pool = DatabasePool(self.database, capacity=1, timeout_s=.01)
        with pool.connection() as connection:
            with self.assertRaises(DatabaseBusyError):
                with pool.connection():
                    self.fail("Exceeded pool capacity")
            pool.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        with self.assertRaises(DatabaseBusyError):
            with pool.connection():
                self.fail("Leased a closed pool")

    def test_hidden_building_access_stays_snappable_and_inspectable(self) -> None:
        graph = driveway_graph()
        enrich(graph, lambda i, record: (0, 90))
        root = self.root / "driveways"
        source = write_graph_outputs(graph, root)["graph"]
        reference = load_path_data(source)
        database = root / "network.sqlite"
        export_network_database(source, database)
        bounds = transform_bounds("EPSG:3067", "EPSG:4326", 375071, 6674999, 375079, 6675001, densify_pts=21)
        lon, lat = transform("EPSG:3067", "EPSG:4326", [375075], [6675000])
        with closing(SQLitePathData(database)) as data:
            self.assertEqual(database_path_config(data), path_config(reference))
            for hidden in (False, True):
                self.assertEqual(database_viewport(data, bounds, 15, hide_building_access=hidden),
                                 viewport_paths(reference, bounds, 15, hide_building_access=hidden))
                self.assertEqual(database_inspection(data, "xy", hide_building_access=hidden),
                                 inspect_path(reference, "xy", hide_building_access=hidden))
            self.assertEqual(database_snap(data, lon[0], lat[0])["snap"]["segment_id"], "xy")
            self.assertFalse(database_viewport(data, bounds, 15)["features"])
            self.assertTrue(database_viewport(data, bounds, 15, hide_building_access=False)["features"])


class SQLiteEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_startup_requires_database_without_loading_json(self) -> None:
        with TemporaryDirectory() as directory:
            graph = write_graph_outputs(fixture(), Path(directory))["graph"]
            environment = {"RUNMAP_ELEVATION_PATH": "fixture", "RUNMAP_PATH_GRAPH": str(graph)}
            with patch.dict(os.environ, environment, clear=True), \
                    patch("runmap.api.app.TerrainRenderer.from_raster", return_value="terrain"), \
                    patch("runmap.api.app.load_path_data", side_effect=AssertionError("loaded full graph")):
                async with lifespan(app):
                    self.assertIsNone(app.state.paths)
                    self.assertEqual(app.state.path_status, "unavailable")

    async def test_lifespan_endpoints_and_etag_use_sqlite_with_no_source_reads(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph = write_graph_outputs(fixture(), root)["graph"]
            database = root / "network.sqlite"
            export_network_database(graph, database)
            environment = {"RUNMAP_ELEVATION_PATH": "fixture", "RUNMAP_PATH_GRAPH": str(graph)}
            with patch.dict(os.environ, environment, clear=True), \
                    patch("runmap.api.app.TerrainRenderer.from_raster", return_value="terrain"), \
                    patch.object(Path, "read_text", side_effect=AssertionError("source read")):
                async with lifespan(app):
                    data = app.state.paths
                    self.assertIsInstance(data, SQLitePathData)
                    self.assertEqual((await get("/api/paths/config"))[0], 200)
                    self.assertEqual((await get("/api/paths/segments/s?reverse=true"))[1]["reverse"], True)
                    self.assertEqual((await get("/api/paths/segments/missing"))[0], 404)
                    self.assertEqual((await get("/api/paths/segments/s?surface=invalid"))[0], 400)
                    self.assertEqual((await get("/api/paths/snap?longitude=24.801&latitude=60.2"))[1]["status"], "ready")
                    self.assertEqual((await get("/api/paths/snap?longitude=24.8&latitude=60.2&dataset_id=stale"))[0], 409)
                    query = "west=24.799&south=60.199&east=24.805&north=60.201&zoom=15"
                    self.assertEqual((await get("/api/paths/viewport?" + query))[0], 200)
                    x, y = xyz(24.801, 60.2, 15)
                    request = Request({"type": "http", "app": app, "headers": []})
                    response = static_path_tile(request, render_version(data), data.dataset_id,
                                                "all-no-stairs", "gradient-hidden", 15, x, y)
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(mapbox_vector_tile.decode(response.body)["paths"]["features"])
                    request = Request({"type": "http", "app": app,
                                       "headers": [(b"if-none-match", response.headers["etag"].encode())]})
                    self.assertEqual(static_path_tile(request, render_version(data), data.dataset_id,
                                                      "all-no-stairs", "gradient-hidden", 15, x, y).status_code, 304)
                    self.assertFalse(hasattr(data, "legacy"))
                self.assertTrue(data.pool.closed)
                self.assertEqual(data.pool.connections.qsize(), 0)

    async def test_missing_stale_or_old_database_fails_without_loading_json(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph = write_graph_outputs(fixture(), root)["graph"]
            database = root / "network.sqlite"
            export_network_database(graph, database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("DELETE FROM metadata WHERE key = 'presentation'")
            for path, expected in ((database, "failed"), (root / "missing.sqlite", "unavailable")):
                environment = {"RUNMAP_ELEVATION_PATH": "fixture", "RUNMAP_PATH_GRAPH": str(graph),
                               "RUNMAP_PATH_DATABASE": str(path)}
                with patch.dict(os.environ, environment, clear=True), \
                        patch("runmap.api.app.TerrainRenderer.from_raster", return_value="terrain"), \
                        patch("runmap.api.app.load_path_data", side_effect=AssertionError("fell back to JSON")), \
                        patch("runmap.api.app.logging.getLogger"):
                    async with lifespan(app):
                        self.assertIsNone(app.state.paths)
                        self.assertEqual(app.state.path_status, expected)

    async def test_missing_routing_source_keeps_display_and_exploration_ready(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph = write_graph_outputs(fixture(), root)["graph"]
            database = root / "network.sqlite"
            export_network_database(graph, database)
            with closing(SQLitePathData(database)) as data:
                app.state.paths = data
                status, config = await get("/api/paths/config")
                self.assertEqual(status, 200)
                self.assertTrue(config["run_planner"]["available"])
                self.assertEqual((await get("/api/paths/segments/s"))[0], 200)
                query = "west=24.799&south=60.199&east=24.805&north=60.201&zoom=15&longitude=24.801&latitude=60.2"
                self.assertEqual((await get("/api/paths/segments/s?" + query))[0], 200)
                self.assertFalse(hasattr(data, "legacy"))


if __name__ == "__main__":
    unittest.main()

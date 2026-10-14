"""Decoded tile semantics, cache lifetimes, and immutable exploration state."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import math
from threading import Event
import unittest
from unittest.mock import patch

import mapbox_vector_tile

from tempfile import TemporaryDirectory
from test_path_api import fixture
from runmap.api.paths import load_path_data
from runmap.paths.graph import write_graph_outputs
from runmap.api.path_tiles import (ByteCache, PathTileService, display_runs, encode_tile,
                                  projected_point, render_version, tile_bounds)


def xyz(longitude: float, latitude: float, z: int) -> tuple[int, int]:
    px, py = projected_point(longitude, latitude, z, 0, 0)
    return math.floor(px / 4096), math.floor(py / 4096)


class PathTileTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = load_path_data(write_graph_outputs(fixture(), directory.name)["graph"])

    def test_decode_identity_orientation_and_missing_grade(self) -> None:
        x, y = xyz(24.8, 60.2, 15)
        decoded = mapbox_vector_tile.decode(encode_tile(self.data, "all-no-stairs", "gradient-all", 15, x, y),
                                            default_options={"y_coord_down": True})["paths"]
        self.assertEqual(decoded["extent"], 4096)
        self.assertEqual({f["properties"]["segment_id"] for f in decoded["features"]}, {"s"})
        missing = [f for f in decoded["features"] if f["properties"]["quality"] == "missing"]
        self.assertEqual(len(missing), 1)
        self.assertNotIn("grade_percent", missing[0]["properties"])
        first = decoded["features"][0]["geometry"]["coordinates"][0]
        expected = projected_point(24.8, 60.2, 15, x, y)
        self.assertLess(abs(first[0] - expected[0]), 1)
        self.assertLess(abs(first[1] - expected[1]), 1)
        self.assertLess(projected_point(24.8, 60.21, 15, x, y)[1], expected[1])

    def test_empty_and_validation(self) -> None:
        self.assertFalse(mapbox_vector_tile.decode(encode_tile(self.data, "all-stairs", "gradient-hidden", 10, 0, 0))["paths"]["features"])
        for args in ((-1, 0, 0), (17, 0, 0), (10, -1, 0), (10, 1024, 0)):
            with self.assertRaises(ValueError):
                tile_bounds(*args)
        with self.assertRaises(ValueError):
            encode_tile(self.data, "invalid", "gradient-all", 10, 0, 0)
        with self.assertRaises(ValueError):
            encode_tile(self.data, "all-stairs", "invalid", 10, 0, 0)

    def test_snapshot_exact_identity_variants_expiry_and_state(self) -> None:
        service = PathTileService(self.data)
        result = service.prepare(24.8, 60.2, .1, "all", True, 50, None)
        same = service.prepare(24.8, 60.2, .1, "all", True, 50, None)
        self.assertEqual(result["snapshot_id"], same["snapshot_id"])
        self.assertTrue(same["snapshot_cache_hit"])
        snapshot = service.snapshot(result["snapshot_id"])
        self.assertEqual(snapshot.state.tolist(), [0, 1, 1, 1])
        self.assertFalse(snapshot.state.flags.writeable)
        self.assertEqual(snapshot.retained_bytes, 552)
        changed_policy = service.prepare(24.8, 60.2, .1, "all", False, 50, None)
        self.assertNotEqual(result["snapshot_id"], changed_policy["snapshot_id"])
        changed_budget = service.prepare(24.8, 60.2, .2, "all", True, 50, None)
        self.assertNotEqual(result["snapshot_id"], changed_budget["snapshot_id"])
        moved = service.prepare(24.80001, 60.2, .1, "all", True, 50, None)
        self.assertNotEqual(result["snapshot_id"], moved["snapshot_id"])
        x, y = xyz(24.8, 60.2, 15)
        with patch("runmap.api.path_tiles.exploration_context", side_effect=AssertionError("routing during tile generation")):
            for variant in ("gradient-all", "gradient-hidden", "distance-all", "distance-hidden"):
                encoded, hit, _ = service.tile(snapshot.policy, variant, 15, x, y, snapshot)
                self.assertFalse(hit)
                self.assertTrue(encoded)
                self.assertTrue(service.tile(snapshot.policy, variant, 15, x, y, snapshot)[1])
        with patch("runmap.api.path_tiles.monotonic", return_value=math.inf):
            with self.assertRaises(KeyError):
                service.snapshot(snapshot.id)
            self.assertEqual(service.snapshots.bytes, 0)

    def test_lod_semantic_boundaries_and_conservative_bins(self) -> None:
        self.data.features[0]["properties"]["grade_percent"] = 2.1
        self.data.features[1]["properties"]["grade_percent"] = 3.9
        runs = display_runs(self.data, [0, 1, 2, 3], "all-stairs", "gradient-all", 11, None)
        # Different minimum thresholds must survive coalescing even if grade bins match.
        self.assertEqual(len(runs), 4)
        self.assertNotEqual(runs[0].properties["flat_min_grade_50m"], runs[1].properties["flat_min_grade_50m"])
        self.data.features[0]["properties"]["flat_min_grade_50m"] = 4
        runs = display_runs(self.data, [0, 1, 2, 3], "all-stairs", "gradient-all", 11, None)
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0].properties["grade_percent"], 4)
        self.assertEqual(len(runs[0].coordinates), 3)
        snapshot_service = PathTileService(self.data)
        result = snapshot_service.prepare(24.8, 60.2, .1, "all", True, 50, None)
        snapshot = snapshot_service.snapshot(result["snapshot_id"])
        runs = display_runs(self.data, [0, 1], "all-no-stairs", "gradient-all", 11, snapshot)
        self.assertEqual(len(runs), 2)
        self.assertNotEqual(runs[0].properties["inclusion_state"], runs[1].properties["inclusion_state"])

    def test_flat_length_features_use_full_samples_before_tile_selection_and_bins(self) -> None:
        graph = fixture()
        intervals = graph["_measurements"]["segments"]["s"]
        for index, interval in enumerate(intervals):
            interval.update(start_m=index * 25, end_m=(index + 1) * 25)
        intervals[0]["grade_percent"] = -2.1
        intervals[1]["grade_percent"] = 2.1
        with TemporaryDirectory() as directory:
            data = load_path_data(write_graph_outputs(graph, directory)["graph"])
        for zoom in (11, 15):
            # Selecting only the first 25 m interval must retain its full-run qualification.
            run = display_runs(data, [0], "all-stairs", "gradient-all", zoom, None)[0]
            self.assertEqual(run.properties["flat_min_grade_50m"], 2.5)
        self.assertNotIn("flat_min_grade_50m", display_runs(data, [3], "all-stairs", "gradient-all", 15, None)[0].properties)
        x, y = xyz(24.8, 60.2, 15)
        decoded = mapbox_vector_tile.decode(encode_tile(data, "all-stairs", "gradient-all", 15, x, y))["paths"]
        self.assertIn(2.5, [f["properties"].get("flat_min_grade_50m") for f in decoded["features"]])

    def test_uncertain_and_stair_samples_cannot_join_flat_runs(self) -> None:
        for flags in (["bridge"], ["tunnel"], ["stairs"]):
            graph = fixture()
            intervals = graph["_measurements"]["segments"]["s"]
            for index, interval in enumerate(intervals):
                interval.update(start_m=index * 20, end_m=(index + 1) * 20,
                                valid=True, grade_percent=1, quality_flags=[])
            intervals[1]["quality_flags"] = flags
            with TemporaryDirectory() as directory:
                data = load_path_data(write_graph_outputs(graph, directory)["graph"])
            self.assertTrue(all(feature["properties"]["flat_min_grade_50m"] is None for feature in data.features))

    def test_buffered_neighbors_keep_shared_segment_and_seam(self) -> None:
        from runmap.api.paths import _cells
        z = 15
        x, y = xyz(24.8, 60.2, z)
        west, south, east, north = tile_bounds(z, x, y)
        geometry = [[west, (south + north) / 2], [east + (east - west), (south + north) / 2]]
        original = self.data.features[0]["geometry"]["coordinates"]
        self.data.features[0]["geometry"]["coordinates"] = geometry
        bounds = (west, south, east + (east - west), north)
        data = replace(self.data, bounds=bounds, cells={cell: [0] for cell in _cells(bounds)})
        try:
            parts = []
            for tx in (x, x + 1):
                tile = mapbox_vector_tile.decode(encode_tile(data, "all-stairs", "gradient-all", z, tx, y),
                                                default_options={"y_coord_down": True})["paths"]
                feature = tile["features"][0]
                self.assertEqual(feature["properties"]["segment_id"], "s")
                parts.append(feature["geometry"]["coordinates"])
            self.assertEqual(parts[0][-1][0], 4160)
            self.assertEqual(parts[1][0][0], -64)
            self.assertEqual(parts[0][-1][1], parts[1][0][1])
        finally:
            self.data.features[0]["geometry"]["coordinates"] = original

    def test_measurement_replacement_changes_immutable_tile_urls(self) -> None:
        graph = {**self.data.graph, "measurements": {**self.data.graph["measurements"], "sha256": "new"}}
        changed = replace(self.data, graph=graph)
        self.assertEqual(changed.dataset_id, self.data.dataset_id)
        self.assertNotEqual(render_version(changed), render_version(self.data))

    def test_requested_marker_details_do_not_enter_snapshot_identity(self) -> None:
        service = PathTileService(self.data)
        first = service.prepare(24.7999, 60.2, 6, "all", True, 50, None)
        second = service.prepare(24.7998, 60.2, 6, "all", True, 50, None)
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertNotEqual(first["start"]["requested"], second["start"]["requested"])
        self.assertNotEqual(first["start"]["snap"]["offset_m"], second["start"]["snap"]["offset_m"])

    def test_snapshot_deadline_and_memory_budget(self) -> None:
        with self.assertRaises(TimeoutError):
            PathTileService(self.data, preparation_deadline=-1).prepare(24.8, 60.2, 6, "all", True, 50, None)
        with self.assertRaises(ValueError):
            PathTileService(self.data, snapshot_bytes=1).prepare(24.8, 60.2, 6, "all", True, 50, None)

    def test_buffered_clipping(self) -> None:
        z = 15
        x, y = xyz(24.8, 60.2, z)
        west, south, east, north = tile_bounds(z, x, y)
        feature = self.data.features[0]
        original = feature["geometry"]["coordinates"]
        feature["geometry"]["coordinates"] = [[west - .01, (south + north) / 2], [east + .01, (south + north) / 2]]
        data = replace(self.data, cells={(cx, cy): [0] for cx, cy in self.data.cells})
        try:
            decoded = mapbox_vector_tile.decode(encode_tile(data, "all-stairs", "gradient-all", z, x, y),
                                                default_options={"y_coord_down": True})["paths"]
            points = decoded["features"][0]["geometry"]["coordinates"]
            self.assertEqual(points[0][0], -64)
            self.assertEqual(points[-1][0], 4160)
        finally:
            feature["geometry"]["coordinates"] = original


class CacheTests(unittest.TestCase):
    def test_coalescing_and_byte_lru(self) -> None:
        cache: ByteCache[bytes] = ByteCache(4, 60)
        entered, release = Event(), Event()
        calls = []

        def factory() -> bytes:
            calls.append(1)
            entered.set()
            release.wait(2)
            return b"ab"

        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(cache.compute, "a", factory, len)
            self.assertTrue(entered.wait(2))
            second = pool.submit(cache.compute, "a", factory, len)
            release.set()
            self.assertEqual(first.result()[0], second.result()[0])
        self.assertEqual(len(calls), 1)
        cache.compute("b", lambda: b"cd", len)
        cache.get("a")
        cache.compute("c", lambda: b"ef", len)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.bytes, 4)


class TileEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_contract_and_dataset_replacement(self) -> None:
        import json
        from tempfile import TemporaryDirectory
        from starlette.types import Message
        from runmap.api.app import app
        from runmap.api.paths import load_path_data
        from runmap.paths.graph import write_graph_outputs
        from test_path_api import fixture

        async def send_request(path: str, payload: dict | None = None,
                               headers: list[tuple[bytes, bytes]] | None = None) -> tuple[int, dict, bytes]:
            messages: list[Message] = []
            delivered = False

            async def receive() -> Message:
                nonlocal delivered
                if delivered:
                    return {"type": "http.disconnect"}
                delivered = True
                return {"type": "http.request", "body": json.dumps(payload).encode() if payload else b"", "more_body": False}

            async def send(message: Message) -> None:
                messages.append(message)

            await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                       "method": "POST" if payload is not None else "GET", "scheme": "http",
                       "path": path, "raw_path": path.encode(), "query_string": b"",
                       "headers": [(b"content-type", b"application/json"), *(headers or [])],
                       "server": ("test", 80), "client": ("test", 1)}, receive, send)
            start = next(message for message in messages if message["type"] == "http.response.start")
            body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
            return start["status"], dict(start["headers"]), body

        with TemporaryDirectory() as directory:
            data = load_path_data(write_graph_outputs(fixture(), directory)["graph"])
            app.state.paths = data
            root = f"/api/paths/tiles/v{render_version(data)}/{data.dataset_id}"
            x, y = xyz(24.8, 60.2, 15)
            path = f"{root}/static/all-no-stairs/gradient-all/15/{x}/{y}.mvt"
            code, headers, body = await send_request(path)
            self.assertEqual(code, 200)
            self.assertIn(b"immutable", headers[b"cache-control"])
            self.assertIn(b"Accept-Encoding", headers[b"vary"])
            self.assertTrue(mapbox_vector_tile.decode(body)["paths"]["features"])
            # A small fixture need not cross the gzip threshold, but validators
            # must remain consistent under content negotiation.
            negotiated = await send_request(path, headers=[(b"accept-encoding", b"gzip")])
            self.assertEqual(negotiated[1][b"etag"], headers[b"etag"])
            self.assertIn(b"Accept-Encoding", negotiated[1][b"vary"])
            self.assertEqual((await send_request(path, headers=[(b"if-none-match", headers[b"etag"])]))[0], 304)
            self.assertEqual((await send_request(path.replace(f"v{render_version(data)}/", "vunknown/")))[0], 404)
            self.assertEqual((await send_request(path.replace(data.dataset_id, "stale")))[0], 409)
            self.assertEqual((await send_request(path.replace("gradient-all", "invalid")))[0], 400)
            code, headers, body = await send_request("/api/paths/snapshots", {
                "dataset_id": data.dataset_id, "longitude": 24.8, "latitude": 60.2})
            self.assertEqual(code, 200)
            self.assertEqual(headers[b"cache-control"], b"no-store")
            snapshot = json.loads(body)
            dynamic = f"{root}/snapshots/{snapshot['snapshot_id']}/distance-all/15/{x}/{y}.mvt"
            code, headers, body = await send_request(dynamic)
            self.assertEqual(code, 200)
            self.assertIn(b"private", headers[b"cache-control"])
            self.assertEqual((await send_request(dynamic.replace(snapshot["snapshot_id"], "expired")))[0], 410)
            # Replacing the loaded object invalidates process-local snapshots even with the same digest.
            app.state.paths = replace(data)
            self.assertEqual((await send_request(dynamic))[0], 410)


class RoutingConcurrencyTests(unittest.TestCase):
    def test_different_starts_do_not_share_a_search_lock(self) -> None:
        from runmap.paths.exploration import ExplorationCache, Snap, DistanceData, cached_distances
        from runmap.api.paths import path_policy_index
        with TemporaryDirectory() as directory:
            data = load_path_data(write_graph_outputs(fixture(), directory)["graph"])
            index = path_policy_index(data)
            cache = ExplorationCache()
            entered, release = Event(), Event()
            first_snap = Snap("s", 0, 0, 24.8, 60.2, 0)
            second_snap = replace(first_snap, graph_offset_m=55)

            def search(graph: object, snap: Snap, maximum: float, deadline: float | None) -> DistanceData:
                if snap.graph_offset_m == 0:
                    entered.set()
                    release.wait(2)
                return DistanceData(snap, maximum, {}, set())

            with patch("runmap.paths.exploration.distance_data", side_effect=search):
                with ThreadPoolExecutor(2) as pool:
                    first = pool.submit(cached_distances, cache, data.dataset_id, index, first_snap, 100)
                    self.assertTrue(entered.wait(2))
                    second = pool.submit(cached_distances, cache, data.dataset_id, index, second_snap, 100)
                    try:
                        self.assertEqual(second.result(timeout=1)[0].snap, second_snap)
                    finally:
                        release.set()
                    self.assertEqual(first.result()[0].snap, first_snap)

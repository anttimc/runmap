"""Decoded tile semantics, cache lifetimes, and immutable exploration state."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from array import array
import math
from threading import Event
import unittest
from unittest.mock import patch

import mapbox_vector_tile

from tempfile import TemporaryDirectory
from test_path_api import fixture
from runmap.api.paths import load_path_data
from runmap.api.paths import _interval_state, candidate_path_indexes, path_policy_index
from runmap.paths.graph import write_graph_outputs
from runmap.paths.measurements import build_measurement_data, indexed_measurement, segment_measurements
from runmap.api.path_tiles import (ByteCache, DisplayRun, PathTileService, Property, display_runs, encode_runs, encode_tile,
                                  projected_point, render_version, snapshot_distances, tile_bounds)
from runmap.paths.exploration import DistanceData, Snap
from runmap.paths.exploration import segment_reason
from runmap.routing.search import freeze_node_distances, external_node_distances


def xyz(longitude: float, latitude: float, z: int) -> tuple[int, int]:
    px, py = projected_point(longitude, latitude, z, 0, 0)
    return math.floor(px / 4096), math.floor(py / 4096)


def tile_coordinate(x: float, y: float) -> tuple[float, float]:
    scale = 2**14 * 4096
    longitude = (8000 * 4096 + x) / scale * 360 - 180
    latitude = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (8000 * 4096 + y) / scale))))
    return longitude, latitude


class PathTileTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = load_path_data(write_graph_outputs(fixture(), directory.name)["graph"])

    def replace_interval(self, number: int, *, grade: float | None = None,
                         geometry: list[list[float]] | None = None) -> None:
        feature = self.data.features[number]
        document = {"schema_version": 1, "graph_digest": self.data.measurements.graph_digest,
                    "method": self.data.measurements.method,
                    "segments": {key: segment_measurements(self.data.measurements, key)
                                 for key in self.data.measurements.segment_ids}}
        item = document["segments"][self.data.measurements.segment_ids[feature.segment]][feature.interval_index]
        if grade is not None:
            item["grade_percent"] = grade
        if geometry is not None:
            item["geometry"] = geometry
        packed = build_measurement_data(self.data.graph, document, index=self.data.routing.integer)
        self.data = replace(self.data, measurements=packed)

    def test_display_intervals_reference_packed_measurements(self) -> None:
        interval = self.data.features[0]
        item = indexed_measurement(self.data.measurements, interval.segment, interval.interval_index)
        self.assertEqual((item["segment_id"], item["interval_index"]), ("s", 0))
        self.assertIs(self.data.measurements.segment_ids, self.data.routing.integer.segment_ids)
        self.assertIs(self.data.measurements.segment_indices, self.data.routing.integer.segment_indices)
        self.assertFalse(hasattr(interval, "__dict__"))
        self.assertFalse(hasattr(interval, "measurement"))

    def test_static_policy_switch_does_not_build_a_routing_index(self) -> None:
        self.assertEqual(self.data.profile_indexes, {})
        with patch("runmap.api.path_tiles.path_policy_index", side_effect=AssertionError("routing index")):
            runs = display_runs(self.data, [0, 1, 2, 3], "surfaced-no-stairs", "gradient-all", 12, None)
        self.assertTrue(runs)
        self.assertEqual(self.data.profile_indexes, {})

    def test_static_display_policy_matches_search_eligibility(self) -> None:
        for tags, restricted_node, missing_arc in (
            ({"surface": "asphalt"}, False, False),
            ({"surface": "ground"}, False, False),
            ({"surface": ""}, False, False),
            ({"highway": "steps"}, False, False),
            ({"foot": "private"}, False, False),
            ({"oneway:foot": "-1"}, False, False),
            ({}, True, False),
            ({}, False, True),
        ):
            with self.subTest(tags=tags, restricted_node=restricted_node, missing_arc=missing_arc):
                graph = fixture()
                graph["segments"][0]["tags"].update(tags)
                if restricted_node:
                    graph["nodes"][0]["foot"] = "no"
                if missing_arc:
                    graph["arcs"] = []
                with TemporaryDirectory() as directory:
                    data = load_path_data(write_graph_outputs(graph, directory)["graph"])
                for surface in ("all", "surfaced"):
                    for stairs in (False, True):
                        policy = f"{surface}-{'no-stairs' if stairs else 'stairs'}"
                        runs = display_runs(data, [0, 1, 2, 3], policy, "gradient-all", 12, None)
                        index = path_policy_index(data, surface, stairs)
                        self.assertEqual({run.properties["eligible"] for run in runs},
                                         {segment_reason(index, "s") is None})

    def test_snapshot_preparation_defers_all_interval_work(self) -> None:
        service = PathTileService(self.data)
        with patch("runmap.api.path_tiles._interval_state", side_effect=AssertionError("eager interval work")):
            result = service.prepare(24.8, 60.2, .1, "all", True, 50, None)
        self.assertEqual(result["interval_preparation_ms"], 0)
        self.assertEqual(self.data.boundary_intervals, {})
        self.assertEqual(service.snapshot(result["snapshot_id"]).budget_km, .1)

    def test_only_requested_tile_candidates_are_evaluated_and_cached(self) -> None:
        graph = fixture()
        far = fixture()
        segment = far["segments"][0]
        segment.update(id="far", **{"from": "3", "to": "4"})
        segment["geometry"] = [[lon + 1, lat] for lon, lat in segment["geometry"]]
        for number, node in enumerate(far["nodes"], 3):
            node.update(id=str(number), longitude=node["longitude"] + 1)
        far["arcs"][0].update(id="far:f", segment_id="far", **{"from": "3", "to": "4"})
        intervals = far["_measurements"]["segments"]["s"]
        for interval in intervals:
            interval.update(segment_id="far", geometry=[[lon + 1, lat] for lon, lat in interval["geometry"]])
        graph["segments"].append(segment)
        graph["nodes"].extend(far["nodes"])
        graph["arcs"].extend(far["arcs"])
        graph["_measurements"]["segments"]["far"] = intervals
        with TemporaryDirectory() as directory:
            data = load_path_data(write_graph_outputs(graph, directory)["graph"])
        service = PathTileService(data)
        result = service.prepare(24.8, 60.2, .1, "all", True, 50, None)
        snapshot = service.snapshot(result["snapshot_id"])
        x, y = xyz(24.8, 60.2, 15)
        west, south, east, north = tile_bounds(15, x, y)
        pad = (east - west) / 64
        expected = candidate_path_indexes(data, (west - pad, south - pad, east + pad, north + pad), False)
        self.assertLess(len(expected), len(data.features))
        with patch("runmap.api.path_tiles._interval_state", wraps=_interval_state) as evaluate:
            encoded, hit, _ = service.tile(snapshot.policy, "distance-all", 15, x, y, snapshot)
            self.assertFalse(hit)
            self.assertEqual(evaluate.call_count, len(expected))
            self.assertEqual({call.args[3]["segment_id"] for call in evaluate.call_args_list}, {"s"})
            self.assertEqual(service.tile(snapshot.policy, "distance-all", 15, x, y, snapshot)[0], encoded)
            self.assertEqual(evaluate.call_count, len(expected))
            # Panning uses the same immutable search; only the new tile is evaluated.
            far_x, far_y = xyz(25.8, 60.2, 15)
            service.tile(snapshot.policy, "distance-all", 15, far_x, far_y, snapshot)
            self.assertIn("far", {call.args[3]["segment_id"] for call in evaluate.call_args_list})

    def test_snapshot_distances_are_isolated_and_memory_scales_with_nodes(self) -> None:
        snap = Snap("s", 0, 0, 24.8, 60.2, 0)
        index = self.data.routing.integer
        assert index is not None
        values = array("d", [0, math.inf])
        outward = freeze_node_distances(index, values, array("q", [0]))
        source = DistanceData(snap, 100, outward)
        frozen, small = snapshot_distances(source)
        values[0] = 99
        self.assertEqual(frozen.outward.values[0], 0)
        self.assertTrue(frozen.outward.values.readonly)
        self.assertIs(frozen, source)
        larger = freeze_node_distances(index, array("d", [0, 50]), array("q", [0, 1]))
        _, large = snapshot_distances(DistanceData(snap, 100, larger))
        self.assertGreater(large, small)

    def test_smaller_budget_uses_cached_search_without_changing_earlier_snapshot(self) -> None:
        service = PathTileService(self.data)
        large_result = service.prepare(24.8, 60.2, .3, "all", True, 50, None)
        small_result = service.prepare(24.8, 60.2, .06, "all", True, 50, None)
        self.assertTrue(small_result["distance_cache_hit"])
        large = service.snapshot(large_result["snapshot_id"])
        small = service.snapshot(small_result["snapshot_id"])
        self.assertEqual(small.distances.maximum_m, large.distances.maximum_m)
        self.assertIs(small.distances.outward, large.distances.outward)
        for snapshot, expected in ((large, ["included"] * 4),
                                   (small, ["included"] + ["beyond_budget"] * 3)):
            states = [display_runs(self.data, [i], snapshot.policy, "distance-all", 15, snapshot)[0]
                      .properties["inclusion_state"] for i in range(4)]
            self.assertEqual(states, expected)

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

    def test_encoded_multipart_lines_preserve_geometry_and_property_types(self) -> None:
        properties: dict[str, Property] = {"segment_id": "Mäkipolku", "eligible": False, "stairs": True,
                                          "distance_from_start_m": 125, "grade_percent": 1.5, "negative": -12}
        # The middle of the line exits the buffered tile; clipping leaves two parts.
        points = ((100, 100), (5000, 100), (5000, 200), (100, 200))
        run = DisplayRun(tuple(tile_coordinate(x, y) for x, y in points), properties, 0)
        tile = mapbox_vector_tile.decode(encode_runs([run], 14, 8000, 8000),
                                         default_options={"y_coord_down": True})["paths"]
        self.assertEqual(tile["extent"], 4096)
        feature = tile["features"][0]
        self.assertEqual(feature["geometry"], {"type": "MultiLineString",
            "coordinates": [[[100, 100], [4160, 100]], [[4160, 200], [100, 200]]]})
        self.assertEqual(feature["properties"], properties)
        self.assertIs(feature["properties"]["eligible"], False)
        self.assertIs(feature["properties"]["stairs"], True)
        self.assertIsInstance(feature["properties"]["negative"], int)
        self.assertIsInstance(feature["properties"]["grade_percent"], float)

    def test_encoding_omits_duplicate_only_and_rounded_zero_length_lines(self) -> None:
        coordinates = ((100., 100.), (100.01, 100.01))
        runs = [DisplayRun(tuple(tile_coordinate(x, y) for x, y in coordinates), {"segment_id": "tiny"}, 0),
                DisplayRun((tile_coordinate(200, 200),) * 2, {"segment_id": "duplicate"}, 0)]
        self.assertEqual(mapbox_vector_tile.decode(encode_runs(runs, 14, 8000, 8000))["paths"]["features"], [])

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
        states = [display_runs(self.data, [i], snapshot.policy, "gradient-all", 15, snapshot)[0]
                  .properties["inclusion_state"] for i in range(4)]
        self.assertEqual(states, ["included", "beyond_budget", "beyond_budget", "beyond_budget"])
        self.assertTrue(snapshot.distances.outward.values.readonly)
        self.assertEqual(external_node_distances(snapshot.distances.outward), {"1": 0})
        self.assertGreater(snapshot.retained_bytes, 512)
        self.assertEqual(service.snapshots.bytes, snapshot.retained_bytes)
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
        self.replace_interval(0, grade=2.1)
        self.replace_interval(1, grade=3.9)
        runs = display_runs(self.data, [0, 1, 2, 3], "all-stairs", "gradient-all", 11, None)
        # Different minimum thresholds must survive coalescing even if grade bins match.
        self.assertEqual(len(runs), 4)
        self.assertNotEqual(runs[0].properties["flat_min_grade_50m"], runs[1].properties["flat_min_grade_50m"])
        self.data = replace(self.data, features=(
            replace(self.data.features[0], flat_min_grade_50m=4), *self.data.features[1:]))
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

    def test_snapshot_tiles_unify_unreachable_and_beyond_budget_intervals(self) -> None:
        service = PathTileService(self.data)
        result = service.prepare(24.802, 60.2, .06, "all", True, 50, None)
        snapshot = service.snapshot(result["snapshot_id"])
        # The one-way path before the start and the distant end share a state.
        states = [display_runs(self.data, [i], snapshot.policy, "gradient-all", 15, snapshot)[0]
                  .properties["inclusion_state"] for i in range(4)]
        self.assertEqual(states, ["beyond_budget", "beyond_budget", "included", "beyond_budget"])
        for variant in ("gradient-all", "distance-all"):
            runs = display_runs(self.data, [0, 1, 2, 3], "all-no-stairs", variant, 15, snapshot)
            self.assertEqual(runs[0].properties["inclusion_state"], "beyond_budget")
            self.assertEqual(runs[-1].properties["inclusion_state"], "beyond_budget")
            x, y = xyz(24.8, 60.2, 15)
            decoded = mapbox_vector_tile.decode(encode_tile(
                self.data, "all-no-stairs", variant, 15, x, y, snapshot))["paths"]
            self.assertEqual({feature["properties"]["inclusion_state"] for feature in decoded["features"]},
                             {"included", "beyond_budget"})

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
            self.assertTrue(all(feature.flat_min_grade_50m is None for feature in data.features))

    def test_buffered_neighbors_keep_shared_segment_and_seam(self) -> None:
        from runmap.api.paths import _cells
        z = 15
        x, y = xyz(24.8, 60.2, z)
        west, south, east, north = tile_bounds(z, x, y)
        geometry = [[west, (south + north) / 2], [east + (east - west), (south + north) / 2]]
        original = segment_measurements(self.data.measurements, "s")[0]["geometry"]
        self.replace_interval(0, geometry=geometry)
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
            self.replace_interval(0, geometry=original)

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
        feature = segment_measurements(self.data.measurements, "s")[0]
        original = feature["geometry"]
        self.replace_interval(0, geometry=[[west - .01, (south + north) / 2], [east + .01, (south + north) / 2]])
        data = replace(self.data, cells={(cx, cy): [0] for cx, cy in self.data.cells})
        try:
            decoded = mapbox_vector_tile.decode(encode_tile(data, "all-stairs", "gradient-all", z, x, y),
                                                default_options={"y_coord_down": True})["paths"]
            points = decoded["features"][0]["geometry"]["coordinates"]
            self.assertEqual(points[0][0], -64)
            self.assertEqual(points[-1][0], 4160)
        finally:
            self.replace_interval(0, geometry=original)


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
                assert index.integer is not None
                outward = freeze_node_distances(index.integer, array("d", [math.inf, math.inf]), array("q"))
                return DistanceData(snap, maximum, outward)

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

"""Shell endpoints reuse snapshots and preserve bounded, valid GeoJSON delivery."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
from unittest.mock import patch

from rasterio.warp import transform, transform_bounds
import numpy as np
from shapely.geometry import GeometryCollection, LineString, Polygon, shape
from starlette.requests import Request

from runmap.api import path_shells
from runmap.api.app import app, distance_shells
from runmap.api.path_shells import PathShellService, generate_bands, shell_features
from runmap.api.path_tiles import PathTileService
from runmap.api.sqlite_data import SQLitePathData
from runmap.paths.database import export_network_database
from runmap.paths.graph import write_graph_outputs
from runmap.paths.numeric_database import export_numeric_database
from runmap.routing.distance_shells import DistanceSamples, ShellSettings
from test_path_api import fixture, get
import numpy as np


class PathShellTests(unittest.IsolatedAsyncioTestCase):
    def test_turnaround_band_geometry_and_feasibility_mask(self) -> None:
        from runmap.api.turnaround import region_features

        snap = self.snapshot.distances.snap
        x, y = transform("EPSG:4326", "EPSG:3067", [snap.longitude], [snap.latitude])
        xx, yy = np.meshgrid(np.arange(-400, 401, 25), np.arange(-400, 401, 25))
        xy = np.column_stack((xx.ravel() + x[0], yy.ravel() + y[0]))
        samples = DistanceSamples(xy, 2 * np.hypot(xx, yy).ravel())
        features = region_features(self.shells, self.snapshot, samples, 500, 0, monotonic() + 20)
        self.assertTrue(any(feature["properties"]["core"] for feature in features))
        self.assertTrue(all(shape(feature["geometry"]).is_valid for feature in features))
        self.assertTrue(all(feature["properties"]["core"] for feature in features))
        uncertain = region_features(self.shells, self.snapshot, samples, 500, .5, monotonic() + 20)
        self.assertEqual({feature["properties"]["region"] for feature in uncertain}, {"target", "uncertainty"})
        self.assertTrue(all(shape(feature["geometry"]).is_valid for feature in uncertain))
        impossible = DistanceSamples(xy, np.full(len(xy), 526.0))
        self.assertEqual(region_features(self.shells, self.snapshot, impossible, 500, 0,
                                        monotonic() + 20), [])

    async def test_turnaround_endpoint_and_cache(self) -> None:
        snapshot_id = self.paths.prepare(24.801, 60.2, 1, "all", True, 50, None)["snapshot_id"]
        with patch.object(app.state, "paths", self.data, create=True), \
                patch.object(app.state, "path_tiles", self.paths, create=True), \
                patch.object(app.state, "path_shells", self.shells, create=True):
            prefix = f"/api/paths/snapshots/{snapshot_id}/turnaround?dataset_id={self.data.dataset_id}"
            status, result = await get(prefix + "&target_m=500&width=0.18")
            self.assertEqual(status, 200, result)
            self.assertEqual(result["target_m"], 500)
            self.assertEqual(result["model"], "cubic-extra-v1")
            self.assertEqual(result["uncertainty"]["nominal_mass"], .8)
            self.assertEqual(result["uncertainty"]["method"], "held-start-residual-v1")
            self.assertEqual(result["snapshot_id"], snapshot_id)
            again = await get(prefix + "&target_m=500&width=0.18")
            self.assertEqual(again[1], result)
            self.assertEqual((await get(prefix + "&target_m=nan"))[0], 400)
            self.assertEqual((await get(prefix + "&target_m=500&width=0.7"))[0], 400)
            self.assertEqual((await get(prefix + "&target_m=2000"))[0], 400)
            self.assertEqual((await get(prefix.replace(self.data.dataset_id, "stale") + "&target_m=500"))[0], 409)
            self.assertEqual((await get(prefix.replace(snapshot_id, "expired") + "&target_m=500"))[0], 410)

    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        graph = fixture()
        graph["experiment"] = {"bounds_epsg3067": transform_bounds("EPSG:4326", "EPSG:3067", 24.79, 60.19, 24.82, 60.21)}
        source = write_graph_outputs(graph, root)["graph"]
        database = root / "network.sqlite"
        export_network_database(source, database)
        export_numeric_database(database, root / "numeric-routing-v2.sqlite")
        self.data = SQLitePathData(database)
        self.addCleanup(self.data.close)
        self.paths = PathTileService(self.data)
        self.snapshot = self.paths.snapshot(self.paths.prepare(24.801, 60.2, .1, "all", True, 50, None)["snapshot_id"])
        self.shells = PathShellService(self.paths)

    async def test_endpoint_identity_validation_expiry_and_both_methods(self) -> None:
        with patch.object(app.state, "paths", self.data, create=True), \
                patch.object(app.state, "path_tiles", self.paths, create=True), \
                patch.object(app.state, "path_shells", self.shells, create=True):
            prefix = f"/api/paths/snapshots/{self.snapshot.id}/shells?dataset_id={self.data.dataset_id}"
            for method in ("grid", "triangulation"):
                status, result = await get(prefix + "&method=" + method)
                self.assertEqual(status, 200)
                self.assertEqual(result["snapshot_id"], self.snapshot.id)
                self.assertEqual(result["method"], method)
                self.assertTrue(result["features"])
                self.assertTrue(all(shape(feature["geometry"]).is_valid for feature in result["features"]))
            status, detour = await get(prefix + "&subtract_straight_line=true")
            self.assertEqual(status, 200)
            self.assertTrue(detour["subtract_straight_line"])
            status, percentage = await get(prefix + "&extra_travel_percentage=true")
            self.assertEqual(status, 200)
            self.assertEqual(percentage["unit"], "percent")
            self.assertEqual((await get(prefix + "&method=unknown"))[0], 422)
            self.assertEqual((await get(prefix + "&cell_m=1"))[0], 400)
            self.assertEqual((await get(prefix + "&smoothing_m=75"))[0], 400)
            self.assertEqual((await get(prefix.replace(self.data.dataset_id, "stale")))[0], 409)
            self.assertEqual((await get(prefix.replace(self.snapshot.id, "expired")))[0], 410)
            with patch("runmap.api.path_tiles.monotonic", return_value=monotonic() + 1000):
                self.assertEqual((await get(prefix))[0], 410)
            status, config = await get("/api/paths/config")
            self.assertEqual(config["shell_methods"], ["grid", "triangulation"])

    def test_sample_reads_coalesce_across_methods_without_display_tiles(self) -> None:
        with patch.object(path_shells, "load_shell_samples", wraps=path_shells.load_shell_samples) as reader, \
                patch("runmap.api.path_tiles.encode_tile", side_effect=AssertionError("Path tile rendered")), \
                ThreadPoolExecutor(max_workers=2) as workers:
            result = list(workers.map(lambda method: self.shells.render(self.snapshot, method), ("grid", "triangulation")))
        self.assertEqual(reader.call_count, 1)
        self.assertTrue(all(json.loads(encoded)["features"] for encoded, _, _ in result))
        self.assertTrue(self.shells.render(self.snapshot, "grid")[1])
        for entry in self.shells.samples.entries.values():
            self.assertFalse(entry.value.xy.flags.writeable)
            self.assertFalse(entry.value.distances.flags.writeable)

    def test_cache_validators_and_snapshot_lifetime(self) -> None:
        scope = {"type": "http", "app": app, "headers": []}
        with patch.object(app.state, "paths", self.data, create=True), \
                patch.object(app.state, "path_tiles", self.paths, create=True), \
                patch.object(app.state, "path_shells", self.shells, create=True):
            response = distance_shells(Request(scope), self.snapshot.id, self.data.dataset_id)
            self.assertEqual(response.status_code, 200)
            cached = distance_shells(Request({**scope, "headers": [(b"if-none-match", response.headers["etag"].encode())]}),
                                     self.snapshot.id, self.data.dataset_id)
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(cached.headers["x-runmap-cache-hit"], "true")
            self.assertIn("private", cached.headers["cache-control"])

    def test_sparse_network_fallback_and_coverage_clipping(self) -> None:
        settings = ShellSettings()
        samples = DistanceSamples(np.array([[375000., 6675000.], [375100., 6675000.], [375200., 6675000.]]),
                                  np.array([0., 100., 200.]))
        bands, used = generate_bands(samples, 500, settings, "triangulation", monotonic() + 5)
        self.assertEqual(used, "grid")
        coverage = Polygon([(375000, 6674900), (375050, 6674900), (375050, 6675100),
                            (375000, 6675100), (375000, 6674900)])
        features, _ = shell_features(bands, coverage)
        self.assertTrue(features)
        self.assertTrue(all(shape(feature["geometry"]).is_valid for feature in features))
        for feature in features:
            for polygon in feature["geometry"]["coordinates"]:
                longitude, latitude = zip(*polygon[0])
                x, y = transform("EPSG:4326", "EPSG:3067", longitude, latitude)
                self.assertGreaterEqual(min(x), 375000 - 1e-6)
                self.assertLessEqual(max(x), 375050 + 1e-6)
                self.assertGreaterEqual(min(y), 6674900 - 1e-6)
                self.assertLessEqual(max(y), 6675100 + 1e-6)
        # Clipping must not discard polygon fragments in GeometryCollections.
        geometry = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
        collection = GeometryCollection([geometry, LineString([(10, 10), (20, 20)])])
        self.assertEqual(path_shells.polygon_parts(collection), (geometry,))

    def test_detour_subtracts_direct_distance_and_preserves_budget(self) -> None:
        samples = DistanceSamples(np.array([[0., 0.], [3., 4.], [6., 8.], [1., 0.]]),
                                  np.array([0., 12., 10., 21.]))
        result = path_shells.detour_samples(samples, (0., 0.), 20)
        np.testing.assert_array_equal(result.distances, [0., 7., 0.])
        np.testing.assert_array_equal(samples.distances, [0., 12., 10., 21.])
        self.assertFalse(result.distances.flags.writeable)

    def test_detour_cache_is_separate_and_reuses_samples(self) -> None:
        with patch.object(path_shells, "load_shell_samples", wraps=path_shells.load_shell_samples) as reader:
            normal, _, _ = self.shells.render(self.snapshot, "grid")
            for method in ("grid", "triangulation"):
                encoded, hit, _ = self.shells.render(self.snapshot, method, subtract_straight_line=True)
                self.assertFalse(hit)
                response = json.loads(encoded)
                self.assertLess(response["maximum_m"], self.snapshot.budget_km * 1000)
                self.assertTrue(all(feature["properties"]["upper_m"] <= response["maximum_m"]
                                    for feature in response["features"]))
                self.assertTrue(json.loads(encoded)["subtract_straight_line"])
                self.assertEqual(json.loads(encoded)["interval_m"], 200)
            self.assertEqual(reader.call_count, 1)
        self.assertFalse(json.loads(normal)["subtract_straight_line"])
        self.assertEqual(json.loads(normal)["interval_m"], 500)
        self.assertTrue(self.shells.render(self.snapshot, "grid", subtract_straight_line=True)[1])

    def test_percentage_samples_use_direct_distance_as_denominator(self) -> None:
        samples = DistanceSamples(np.array([[0., 0.], [300., 400.], [600., 800.], [1., 0.]]),
                                  np.array([0., 750., 1000., 2000.]))
        result = path_shells.detour_samples(samples, (0., 0.), 1500, percentage=True)
        np.testing.assert_array_equal(result.distances, [0., 50., 0.])
        np.testing.assert_array_equal(samples.distances, [0., 750., 1000., 2000.])
        self.assertTrue(np.isfinite(result.distances).all())
        self.assertFalse(result.distances.flags.writeable)

    def test_percentage_regularizes_small_distances_only(self) -> None:
        samples = DistanceSamples(np.array([[0., 0.], [0.001, 0.], [3., 4.],
                                           [30., 40.], [60., 80.], [300., 400.], [600., 800.]]),
                                  np.array([0., 10.001, 15., 75., 150., 750., 1500.]))
        result = path_shells.detour_samples(samples, (0., 0.), 2000, percentage=True)
        np.testing.assert_allclose(result.distances, [0., 2., 2., 5., 10., 50., 50.])
        distance = path_shells.detour_samples(samples, (0., 0.), 2000)
        np.testing.assert_allclose(distance.distances, [0., 10., 10., 25., 50., 250., 500.])

    def test_percentage_cache_and_scale_for_both_methods(self) -> None:
        with patch.object(path_shells, "load_shell_samples", wraps=path_shells.load_shell_samples) as reader:
            for method in ("grid", "triangulation"):
                self.shells.render(self.snapshot, method, subtract_straight_line=True)
                encoded, hit, _ = self.shells.render(self.snapshot, method, extra_travel_percentage=True)
                result = json.loads(encoded)
                self.assertFalse(hit)
                self.assertTrue(result["extra_travel_percentage"])
                self.assertTrue(result["subtract_straight_line"])
                self.assertEqual(result["unit"], "percent")
                self.assertGreaterEqual(result["maximum"], 1)
                self.assertGreaterEqual(result["interval"], 1)
                self.assertLessEqual(result["maximum"] / result["interval"], 20)
                self.assertTrue(self.shells.render(self.snapshot, method, extra_travel_percentage=True)[1])
            self.assertEqual(reader.call_count, 1)

    def test_percentage_scale_saturates_without_discarding_high_values(self) -> None:
        points = np.array([[float(x), float(y)] for y in range(0, 250, 50)
                           for x in range(0, 250, 50)])
        values = np.full(len(points), 400.)
        samples = DistanceSamples(points, values)
        for method in ("grid", "triangulation"):
            bands, used, maximum, interval = path_shells.percentage_bands(
                samples, ShellSettings(smoothing_m=50), method, monotonic() + 5)
            self.assertTrue(bands)
            self.assertEqual(maximum, bands[-1].upper_m)
            self.assertAlmostEqual(maximum, 400)
            self.assertEqual(interval, 27)
            self.assertTrue(bands[-1].polygons)
            np.testing.assert_array_equal(samples.distances, values)

    def test_percentage_scale_caps_at_99th_percentile(self) -> None:
        values = np.r_[np.arange(100, dtype=np.float64), 10000., np.nan]
        maximum, interval = path_shells.percentage_scale(values)
        self.assertEqual(maximum, 99)
        self.assertEqual(interval, 7)
        self.assertEqual(path_shells.percentage_scale(np.zeros(10)), (1., 1.))

    def test_smoothing_cache_and_validation(self) -> None:
        with patch.object(path_shells, "load_shell_samples", wraps=path_shells.load_shell_samples) as reader:
            for smoothing in (0, 25, 50):
                encoded, hit, _ = self.shells.render(self.snapshot, "grid", smoothing_m=smoothing)
                self.assertFalse(hit)
                result = json.loads(encoded)
                self.assertEqual(result["smoothing_used_m"], smoothing)
                self.assertEqual(result["smoothing_m"], smoothing)
                self.assertTrue(self.shells.render(self.snapshot, "grid", smoothing_m=smoothing)[1])
            self.assertEqual(reader.call_count, 1)
        with self.assertRaises(ValueError):
            self.shells.render(self.snapshot, "grid", smoothing_m=75)

    def test_snapshot_distances_are_not_mutated(self) -> None:
        before = self.snapshot.distances.outward.values.copy()
        self.shells.render(self.snapshot, "grid")
        np.testing.assert_array_equal(self.snapshot.distances.outward.values, before)


if __name__ == "__main__":
    unittest.main()

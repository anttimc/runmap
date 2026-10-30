"""Verify route profiles use absolute heights, ordered traversals, and explicit gaps."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from contextlib import closing

import numpy as np
import rasterio
from rasterio.warp import transform

from runmap.api.route_profile import build_route_profile, route_spans, sample_heights
from runmap.api.runs import TraversalResponse
from runmap.api.app import app
from runmap.api.paths import load_path_data
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.terrain_tiles import TerrainTileService
from runmap.paths.database import export_network_database
from runmap.paths.graph import write_graph_outputs
from test_path_api import fixture, get
from runmap.elevation.store import ElevationStore
from runmap.regions import Tile
from runmap.routing.types import Segment


def segment() -> Segment:
    xs, ys = transform("EPSG:3067", "EPSG:4326", [370010, 370110], [6680010, 6680010])
    return {"id": "s", "osm_way_id": "1", "from": "a", "to": "b", "length_m": 100,
            "geometry": list(zip(xs, ys)), "tags": {"highway": "path"}}


def traversal(start: float, end: float) -> TraversalResponse:
    return TraversalResponse(segment_id="s", start_m=start, end_m=end)


class RouteProfileTests(unittest.TestCase):
    def test_partial_out_and_back_has_identical_heights_and_balanced_totals(self) -> None:
        def heights(paths: list[Path], points: list[tuple[float, float]]) -> list[float]:
            xs, _ = transform("EPSG:4326", "EPSG:3067", *zip(*points))
            return [25 + (x - 370010) / 10 for x in xs]

        with patch("runmap.api.route_profile.sample_heights", side_effect=heights):
            result = build_route_profile("dataset", "manifest", 40,
                                         [[traversal(15, 85)], [traversal(85, 15)]], {"s": segment()}, [])
        self.assertEqual(result.distance_m, 140)
        self.assertEqual(result.turnaround_m, 70)
        self.assertTrue(result.totals_complete)
        self.assertAlmostEqual(result.ascent_m, 7, places=5)
        self.assertAlmostEqual(result.descent_m, 7, places=5)
        first, last = result.intervals[0], result.intervals[-1]
        self.assertEqual(first.start_elevation_m, last.end_elevation_m)
        self.assertGreater(first.grade_percent, 0)
        self.assertLess(last.grade_percent, 0)
        self.assertAlmostEqual(first.start_elevation_m, 26.5, places=5)

    def test_missing_elevations_break_profile_and_do_not_count_as_flat(self) -> None:
        def heights(paths: list[Path], points: list[tuple[float, float]]) -> list[float | None]:
            return [None if i == 3 else float(i + 20) for i in range(len(points))]

        with patch("runmap.api.route_profile.sample_heights", side_effect=heights):
            result = build_route_profile("dataset", "manifest", 40, [[traversal(0, 100)]], {"s": segment()}, [])
        self.assertFalse(result.totals_complete)
        self.assertAlmostEqual(result.valid_length_fraction, .8)
        self.assertEqual(sum(item.grade_percent is None for item in result.intervals), 2)
        self.assertEqual(result.intervals[4].start_elevation_m, 24)
        self.assertEqual(result.ascent_m, 8)

    def test_unavailable_heights_and_ground_uncertainty_are_explicit(self) -> None:
        road = segment()
        road["tags"]["bridge"] = "yes"
        result = build_route_profile("dataset", "manifest", 40, [[traversal(0, 100)]], {"s": road}, [])
        self.assertEqual(result.valid_length_fraction, 0)
        self.assertFalse(result.totals_complete)
        self.assertEqual(result.intervals[0].quality_flags, ["bridge", "missing_terrain"])

    def test_rejects_disconnected_and_out_of_bounds_traversals(self) -> None:
        for legs in ([[traversal(101, 0)]], [[traversal(0, 50), traversal(80, 100)]],
                     [[traversal(float("nan"), 10)]], [[]]):
            with self.subTest(legs=legs), self.assertRaises(ValueError):
                route_spans(legs, {"s": segment()})

    def test_smoothed_height_cog_preserves_elevations_and_bilinear_sampling(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "height.tif"
            values = np.arange(16, dtype=np.float64).reshape(4, 4) + 10
            ElevationStore._write_height_cog(path, Tile(370000, 6680000, 8), values, 40, "source")
            with rasterio.open(path) as dataset:
                np.testing.assert_array_equal(dataset.read(1), values)
                self.assertTrue(dataset.is_tiled)
                self.assertEqual(dataset.tags()["wavelength_m"], "40")
            xs, ys = transform("EPSG:3067", "EPSG:4326", [370002, 370100], [6680006, 6680006])
            sampled = sample_heights([path], list(zip(xs, ys)))
            self.assertAlmostEqual(sampled[0], 12.5, places=5)
            self.assertIsNone(sampled[1])
            # A nodata neighbour prevents interpolation across unknown terrain.
            values[1, 1] = np.nan
            ElevationStore._write_height_cog(path, Tile(370000, 6680000, 8), values, 40, "source")
            self.assertIsNone(sample_heights([path], [(xs[0], ys[0])])[0])


class RouteProfileEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_and_sqlite_profiles_pin_terrain_manifest_and_ignore_hill_settings(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = write_graph_outputs(fixture(), root)
            database = root / "network.sqlite"
            export_network_database(outputs["graph"], database)
            height = root / "terrain.height.tif"
            height.touch()
            terrain = TerrainTileService.__new__(TerrainTileService)
            terrain.store = ElevationStore(root)
            terrain.manifest_id = "different-active-manifest"
            with closing(SQLitePathData(database)) as sqlite:
                for data in (load_path_data(outputs["graph"]), sqlite):
                    with patch.object(app.state, "paths", data, create=True), \
                            patch.object(app.state, "terrain", terrain, create=True), \
                            patch.object(terrain.store, "ready_derived_paths", return_value=(root / "terrain.tif",)) as ready, \
                            patch("runmap.api.route_profile.sample_heights", side_effect=lambda paths, points: [25.0] * len(points)):
                        body = {"dataset_id": data.dataset_id, "legs": [[{"segment_id": "s", "start_m": 25, "end_m": 150}]]}
                        status, result = await get("/api/paths/run/profile", body)
                        self.assertEqual(status, 200)
                        self.assertEqual(result["distance_m"], 125)
                        self.assertTrue(result["totals_complete"])
                        self.assertEqual(result["intervals"][0]["start_elevation_m"], 25)
                        ready.assert_called_with("fixture", 40)
                        self.assertEqual((await get("/api/paths/run/profile", {**body, "dataset_id": "stale"}))[0], 409)
                        self.assertEqual((await get("/api/paths/run/profile", {**body, "legs": [[{"segment_id": "missing", "start_m": 0, "end_m": 10}]]}))[0], 404)
                        self.assertEqual((await get("/api/paths/run/profile", {**body, "legs": [[{"segment_id": "s", "start_m": -1, "end_m": 10}]]}))[0], 400)
                        self.assertEqual((await get("/api/paths/run/profile", {**body, "legs": []}))[0], 422)
                        self.assertEqual((await get("/api/paths/segments/s"))[0], 404)


if __name__ == "__main__":
    unittest.main()

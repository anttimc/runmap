"""Environment acquisition preserves source evidence and resumable identity."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

try:
    import geopandas as gpd
    import osmnx as ox
    import pandas as pd
    from shapely.geometry import Polygon
except ImportError:
    ENVIRONMENT_AVAILABLE = False
else:
    ENVIRONMENT_AVAILABLE = True

from runmap.sources.environment import acquire_environment, load_request, query_bounds


CONFIG = Path("config/environment/leppavaara-pilot.json")


@unittest.skipUnless(ENVIRONMENT_AVAILABLE, "Requires the environment extra")
class EnvironmentAcquisitionTests(unittest.TestCase):
    def test_rejects_unsafe_or_unbounded_configs(self) -> None:
        original = json.loads(CONFIG.read_text())
        cases = [{"id": "../escape"}, {"halo_m": 0}, {"halo_m": float("nan")},
                 {"bounds_epsg3067": [0, 0, 40000, 40000]}, {"tags": {"highway": False}}]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            for changes in cases:
                with self.subTest(changes=changes):
                    path.write_text(json.dumps(original | changes))
                    with self.assertRaises(ValueError):
                        load_request(path)

    def test_projected_core_is_inside_query_with_halo(self) -> None:
        from rasterio.warp import transform

        request = load_request(CONFIG)
        left, bottom, right, top = query_bounds(request)
        west, south, east, north = request.bounds_epsg3067
        xs, ys = transform("EPSG:3067", "EPSG:4326", [west, east], [south, north])
        for x, y in zip(xs, ys):
            self.assertTrue(left < x < right)
            self.assertTrue(bottom < y < top)

    def test_preserves_holes_ids_tags_and_raw_source_then_refuses_overwrite(self) -> None:
        polygon = Polygon([(24.8, 60.2), (24.9, 60.2), (24.9, 60.3), (24.8, 60.3)],
                          [[(24.82, 60.22), (24.84, 60.22), (24.84, 60.24), (24.82, 60.24)]])
        frame = gpd.GeoDataFrame({"natural": ["wood"], "leaf_type": ["mixed"], "highway": [None]},
                                index=pd.MultiIndex.from_tuples([("relation", 42)]),
                                geometry=[polygon], crs="EPSG:4326")
        request = load_request(CONFIG)
        previous_cache = ox.settings.cache_folder
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "pilot"

            def download(bounds: tuple[float, float, float, float], tags: dict[str, bool | list[str]]) -> gpd.GeoDataFrame:
                cache = Path(ox.settings.cache_folder)
                cache.mkdir(parents=True)
                (cache / "response.json").write_text(json.dumps({
                    "elements": [], "osm3s": {"timestamp_osm_base": "2026-11-02T00:00:00Z"},
                }))
                return frame

            with patch.object(ox.features, "features_from_bbox", side_effect=download) as fetch:
                manifest_path = acquire_environment(request, destination)
                fetch.assert_called_once_with(query_bounds(request), request.tags)
            feature = json.loads((destination / "features.geojson").read_text())["features"][0]
            self.assertEqual(len(feature["geometry"]["coordinates"]), 2)
            self.assertEqual(feature["properties"]["osm_id"], 42)
            self.assertEqual(feature["properties"]["leaf_type"], "mixed")
            self.assertNotIn("highway", feature["properties"])
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["invalid_geometry_count"], 0)
            self.assertEqual(len(manifest["raw_responses"]), 1)
            with self.assertRaises(FileExistsError):
                acquire_environment(request, destination)
        self.assertEqual(ox.settings.cache_folder, previous_cache)

    def test_interrupted_run_resumes_only_for_matching_request(self) -> None:
        request = load_request(CONFIG)
        previous_cache = ox.settings.cache_folder
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "pilot"
            with patch.object(ox.features, "features_from_bbox", side_effect=ConnectionError("offline")) as fetch:
                for _ in range(2):
                    with self.assertRaises(ConnectionError):
                        acquire_environment(request, destination)
                self.assertEqual(fetch.call_count, 2)
                path = destination / "request.json"
                record = json.loads(path.read_text())
                record["halo_m"] = 999
                path.write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    acquire_environment(request, destination)
                self.assertEqual(fetch.call_count, 2)
        self.assertEqual(ox.settings.cache_folder, previous_cache)


if __name__ == "__main__":
    unittest.main()

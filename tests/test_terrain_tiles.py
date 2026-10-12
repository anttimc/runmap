from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import math
from threading import Lock
from time import sleep
import unittest
from unittest.mock import patch

from PIL import Image
import numpy as np
import rasterio
from rasterio.transform import from_bounds

from runmap.api.terrain_tiles import (
    CONTOUR_INTERVAL_M,
    TerrainTileService,
    WEB_MERCATOR_LIMIT,
    _encode_contour_mvt,
    xyz_bounds,
)
from runmap.regions import load_region


class FakeStore:
    def active_manifest_for_region(self, region):
        return "test-manifest"


class DerivedStore(FakeStore):
    def __init__(self, path):
        self.path = path

    def ensure_derived_tile(self, manifest_id, tile, wavelength):
        return self.path


class TerrainTileTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        path = Path(self.directory.name) / "area.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "id": "area",
            "title": "Test area",
            "bounds_epsg3067": [370000, 6675000, 375000, 6680000],
            "acquisition_policy": "preload",
            "public": True,
            "initial_view": {"zoom": 12},
            "attribution_label": "Elevation model 2 m",
        }))
        self.service = TerrainTileService(load_region(path), FakeStore())

    def tearDown(self):
        self.directory.cleanup()

    def test_xyz_zero_covers_web_mercator_world(self):
        self.assertEqual(
            xyz_bounds(0, 0, 0),
            (-WEB_MERCATOR_LIMIT, -WEB_MERCATOR_LIMIT, WEB_MERCATOR_LIMIT, WEB_MERCATOR_LIMIT),
        )

    def test_config_advertises_versioned_raster_tiles_and_presets(self):
        config = self.service.config()
        self.assertIn("/api/regions/area/terrain/v6/", config["terrain_tiles"]["template"])
        self.assertIn("/test-manifest/", config["terrain_tiles"]["template"])
        self.assertEqual(config["smoothing"]["values"], [0, 20, 40, 60, 80, 100])
        self.assertIn("/contours/v2/", config["contour_tiles"]["template"])
        self.assertIn("/test-manifest/", config["contour_tiles"]["template"])
        self.assertTrue(config["contour_tiles"]["template"].endswith(".mvt"))
        self.assertEqual(config["contour_tiles"]["interval_m"], 5)

    def test_outside_tile_is_transparent_without_analysis(self):
        image_bytes, elapsed = self.service.render_tile(40, 8, 0, 0)
        image = Image.open(BytesIO(image_bytes))
        self.assertEqual(image.size, (256, 256))
        self.assertEqual(image.mode, "P")
        self.assertEqual(image.getpixel((128, 128)), 0)
        self.assertGreater(elapsed, 0)

    def test_rejects_unadvertised_wavelength(self):
        with self.assertRaisesRegex(ValueError, "one of"):
            self.service.render_tile(42, 12, 0, 0)

    def test_terrain_reprojects_derived_cog_directly(self):
        derived = Path(self.directory.name) / "derived.tif"
        values = np.full((64, 64), 1000, dtype=np.uint16)
        with rasterio.open(
            derived,
            "w",
            driver="GTiff",
            width=values.shape[1],
            height=values.shape[0],
            count=1,
            dtype=values.dtype,
            crs="EPSG:3067",
            transform=from_bounds(370000, 6675000, 375000, 6680000, 64, 64),
            nodata=65535,
        ) as dataset:
            dataset.write(values, 1)
        service = TerrainTileService(self.service.region, DerivedStore(derived))
        west, south, east, north = service.bounds_wgs84
        longitude = (west + east) / 2
        latitude = (south + north) / 2
        zoom = 15
        x = int((longitude + 180) / 360 * (1 << zoom))
        y = int(
            (1 - math.asinh(math.tan(math.radians(latitude))) / math.pi)
            / 2
            * (1 << zoom)
        )

        image_bytes, _ = service.render_tile(40, zoom, x, y)
        alpha = Image.open(BytesIO(image_bytes)).convert("RGBA").getchannel("A")

        self.assertGreater(alpha.getextrema()[1], 0)

    def test_contour_renderer_encodes_five_metre_vector_lines(self):
        rows, cols = np.mgrid[0:520, 0:520]
        elevation = (cols / 20).astype("float32")
        tile = _encode_contour_mvt(elevation)

        self.assertEqual(CONTOUR_INTERVAL_M, 5)
        self.assertGreater(len(tile), 100)
        self.assertIn(b"contours", tile)
        self.assertIn(b"elevation", tile)
        self.assertIn(b"index", tile)

    def test_contours_below_zero_encode_signed_int64_elevations(self):
        _, cols = np.mgrid[0:520, 0:520]
        tile = _encode_contour_mvt((cols / 20 - 10).astype("float32"))
        # Value.int_value (field 4) stores -5 and -10 as ten-byte int64 varints.
        self.assertIn(b"\x20\xfb" + b"\xff" * 8 + b"\x01", tile)
        self.assertIn(b"\x20\xf6" + b"\xff" * 8 + b"\x01", tile)

    def test_rejects_contours_outside_advertised_zoom_range(self):
        with self.assertRaisesRegex(ValueError, "contour tile range"):
            self.service.render_contour_tile(11, 0, 0)

    def test_distinct_tile_keys_render_concurrently(self):
        active = 0
        maximum_active = 0
        state_lock = Lock()

        def render(*args):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            sleep(0.05)
            with state_lock:
                active -= 1
            return b"tile"

        with patch.object(self.service, "_render_uncached", side_effect=render):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda x: self.service.render_tile(40, 12, x, 0),
                    (0, 1),
                ))

        self.assertEqual(maximum_active, 2)
        self.assertEqual([result[0] for result in results], [b"tile", b"tile"])

    def test_duplicate_tile_requests_share_one_inflight_render(self):
        calls = 0
        state_lock = Lock()

        def render(*args):
            nonlocal calls
            with state_lock:
                calls += 1
            sleep(0.05)
            return b"shared"

        with patch.object(self.service, "_render_uncached", side_effect=render):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(
                    lambda _: self.service.render_tile(40, 12, 0, 0),
                    range(4),
                ))

        self.assertEqual(calls, 1)
        self.assertEqual([result[0] for result in results], [b"shared"] * 4)
        self.assertEqual(sum(result[1] > 0 for result in results), 1)

    def test_failed_inflight_render_is_shared_and_can_be_retried(self):
        calls = 0

        def fail(*args):
            nonlocal calls
            calls += 1
            sleep(0.05)
            raise RuntimeError("render failed")

        with patch.object(self.service, "_render_uncached", side_effect=fail):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(self.service.render_tile, 40, 12, 0, 0)
                    for _ in range(2)
                ]
                for future in futures:
                    with self.assertRaisesRegex(RuntimeError, "render failed"):
                        future.result()

        with patch.object(self.service, "_render_uncached", return_value=b"recovered"):
            result, _ = self.service.render_tile(40, 12, 0, 0)

        self.assertEqual(calls, 1)
        self.assertEqual(result, b"recovered")


if __name__ == "__main__":
    unittest.main()

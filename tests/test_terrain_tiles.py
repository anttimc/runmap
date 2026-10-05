from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import math
import unittest

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
        self.assertIn("/api/regions/area/terrain/v5/", config["terrain_tiles"]["template"])
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

    def test_rejects_contours_outside_advertised_zoom_range(self):
        with self.assertRaisesRegex(ValueError, "contour tile range"):
            self.service.render_contour_tile(11, 0, 0)


if __name__ == "__main__":
    unittest.main()

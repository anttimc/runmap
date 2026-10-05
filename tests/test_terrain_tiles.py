from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from PIL import Image

from runmap.api.terrain_tiles import TerrainTileService, WEB_MERCATOR_LIMIT, xyz_bounds
from runmap.regions import load_region


class FakeStore:
    def active_manifest_for_region(self, region):
        return "test-manifest"


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
        self.assertIn("/api/regions/area/terrain/v4/", config["terrain_tiles"]["template"])
        self.assertEqual(config["smoothing"]["values"], [0, 20, 40, 60, 80, 100])

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


if __name__ == "__main__":
    unittest.main()

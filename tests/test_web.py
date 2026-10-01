import unittest
from io import BytesIO

import numpy as np
from PIL import Image

from runmap.api.terrain import TerrainRenderer


class TerrainRendererTests(unittest.TestCase):
    def setUp(self):
        rows, cols = np.mgrid[0:64, 0:80]
        elevation = 10 + 0.02 * cols + 2 * np.exp(-((cols - 40) ** 2 + (rows - 32) ** 2) / 100)
        self.renderer = TerrainRenderer(elevation, 2, (24.7, 60.2, 24.8, 60.3))

    def test_config_describes_map_and_default(self):
        config = self.renderer.config()
        self.assertEqual(config["source_shape"], [64, 80])
        self.assertEqual(config["smoothing"]["default"], 40)
        self.assertEqual(config["image_coordinates"][0], [24.7, 60.3])

    def test_render_returns_bounded_palette_png_and_caches_it(self):
        first, elapsed = self.renderer.render_png(40, 256)
        second, cached_elapsed = self.renderer.render_png(40, 256)
        image = Image.open(BytesIO(first))
        self.assertEqual(image.mode, "P")
        self.assertEqual(image.size, (80, 64))
        self.assertGreater(elapsed, 0)
        self.assertEqual(cached_elapsed, 0)
        self.assertEqual(first, second)

    def test_render_rejects_out_of_range_settings(self):
        with self.assertRaises(ValueError):
            self.renderer.render_png(101)


if __name__ == "__main__":
    unittest.main()

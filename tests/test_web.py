import unittest
from io import BytesIO

import numpy as np
from PIL import Image
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import transform

from runmap.api.terrain import colorize_slope_png, slope_opacity, TerrainRenderer


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

    def test_slope_png_uses_continuous_colours_and_smooth_low_slope_transparency(self):
        slopes = np.linspace(0, 40, 121, dtype=np.float64).reshape(1, -1)
        image = Image.open(BytesIO(colorize_slope_png(slopes)))

        palette = np.asarray(image.getpalette(), dtype=np.uint8).reshape(-1, 3)
        used_colours = {tuple(palette[index]) for index in set(image.getdata())}
        transparency = image.info["transparency"]
        self.assertGreater(len(used_colours), 100)
        self.assertEqual(transparency[1], 0)
        self.assertEqual(transparency[1 + round(254 * 15 / 40)], 255)
        self.assertAlmostEqual(float(slope_opacity(7.5)), 0.5)

    def test_raster_corners_are_transformed_instead_of_using_bounds_envelope(self):
        raster_transform = from_origin(374000, 6682000, 2, 2)
        values = np.arange(20, dtype=np.float32).reshape(4, 5)
        with MemoryFile() as memory:
            with memory.open(
                driver="GTiff",
                width=values.shape[1],
                height=values.shape[0],
                count=1,
                dtype=values.dtype,
                crs="EPSG:3067",
                transform=raster_transform,
            ) as dataset:
                dataset.write(values, 1)
            renderer = TerrainRenderer.from_raster(memory.name)

        left, top = 374000, 6682000
        right = left + 2 * values.shape[1]
        bottom = top - 2 * values.shape[0]
        longitude, latitude = transform(
            "EPSG:3067",
            "EPSG:4326",
            [left, right, right, left],
            [top, top, bottom, bottom],
        )
        expected = [[x, y] for x, y in zip(longitude, latitude)]
        config = renderer.config()

        np.testing.assert_allclose(config["image_coordinates"], expected)
        self.assertNotEqual(
            config["image_coordinates"][0][1],
            config["bounds_wgs84"][3],
        )

    def test_grid_renderer_includes_region_metadata(self):
        values = np.ones((4, 5), dtype=np.float32)
        renderer = TerrainRenderer.from_grid(
            values,
            from_origin(370000, 6685000, 2, 2),
            "EPSG:3067",
            metadata={"region": {"id": "area"}},
        )

        config = renderer.config()
        self.assertEqual(config["region"]["id"], "area")
        self.assertEqual(config["source_shape"], [4, 5])


if __name__ == "__main__":
    unittest.main()

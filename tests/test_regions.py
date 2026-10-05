import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin

from runmap.elevation.store import ElevationStore
from runmap.regions import load_region


def region_document(bounds=None):
    return {
        "schema_version": 1,
        "id": "area",
        "title": "Test area",
        "bounds_epsg3067": bounds or [370000, 6675000, 380000, 6685000],
        "acquisition_policy": "preload",
        "public": True,
        "initial_view": {"zoom": 12},
        "attribution_label": "Elevation model 2 m",
    }


class RegionTests(unittest.TestCase):
    def test_grid_aligned_ten_kilometre_region_has_four_shared_tiles(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "area.json"
            path.write_text(json.dumps(region_document()))
            region = load_region(path)

        self.assertEqual(region.shape, (5000, 5000))
        self.assertEqual(
            [tile.id for tile in region.source_tiles()],
            [
                "e0370000_n6675000",
                "e0375000_n6675000",
                "e0370000_n6680000",
                "e0375000_n6680000",
            ],
        )
        self.assertEqual(len(region.source_tiles(512)), 16)

    def test_region_bounds_must_align_to_native_grid(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "area.json"
            path.write_text(json.dumps(region_document([370001, 6675000, 380000, 6685000])))
            with self.assertRaisesRegex(ValueError, "2 m grid"):
                load_region(path)

    def test_prepared_cog_preserves_grid_and_values(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.tif"
            destination = Path(directory) / "prepared.tif"
            values = np.arange(64, dtype=np.float32).reshape(8, 8)
            with rasterio.open(
                source,
                "w",
                driver="GTiff",
                width=8,
                height=8,
                count=1,
                dtype="float32",
                crs="EPSG:3067",
                transform=from_origin(370000, 6685000, 2, 2),
            ) as dataset:
                dataset.write(values, 1)

            ElevationStore._prepare_cog(source, destination)

            with rasterio.open(destination) as dataset:
                self.assertTrue(dataset.is_tiled)
                self.assertEqual(dataset.crs.to_epsg(), 3067)
                np.testing.assert_array_equal(dataset.read(1), values)


if __name__ == "__main__":
    unittest.main()

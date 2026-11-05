"""Check regional seams, bounded tile rendering, missing data, and bundle identities."""

from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import numpy as np
from PIL import Image
import rasterio
from rasterio.warp import transform_bounds, transform_geom
from shapely.geometry import LineString, box, mapping

from runmap.api.environment_tiles import environment_tile, environment_tile_config, render_tile
from runmap.environment.fields import build_fields, make_grid
from runmap.environment.objects import EnvironmentObject
from runmap.environment.prepare import DEFAULT_CONFIG, load_config, write_raster
from runmap.environment.regional import block_fields, context_radius, prepare_regional
from runmap.sources.environment import file_identity, load_request


class EnvironmentTileTests(unittest.TestCase):
    def test_block_scores_agree_with_whole_grid_across_seam(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        halo = context_radius(config)
        objects = [EnvironmentObject("way/1", {}, box(374100, 6674000, 374300, 6674400), ("forest", "wetland")),
                   EnvironmentObject("way/2", {}, box(374180, 6674120, 374220, 6674300), ("lake",)),
                   EnvironmentObject("way/3", {"highway": "motorway"},
                                     LineString([(374260, 6673500), (374260, 6674900)]), ("road",))]
        west = block_fields(objects, [374000, 6674000, 374200, 6674400], config, halo)
        east = block_fields(objects, [374200, 6674000, 374400, 6674400], config, halo)
        full = build_fields(objects, make_grid([374000-halo, 6674000-halo, 374400+halo, 6674400+halo], 10), config)
        offset = halo // 10
        for field in ("greenery", "water", "traffic", "wetland", "context_support", "traffic_context_support"):
            expected = full[field][offset:offset+40, offset:offset+40]
            np.testing.assert_allclose(np.concatenate([west[field], east[field]], axis=1), expected, atol=1e-6)

    def test_regional_publication_writes_windows_and_refuses_overwrite(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source", root / "prepared"
            source.mkdir()
            request = json.loads(Path("config/environment/helsinki-capital-area.json").read_text())
            request["bounds_epsg3067"] = [374000, 6674000, 374400, 6674400]
            (source / "request.json").write_text(json.dumps(request))
            geometry = transform_geom("EPSG:3067", "EPSG:4326", mapping(box(373000, 6673000, 375000, 6675000)))
            (source / "features.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"osm_type": "way", "osm_id": 1, "natural": "wood"}, "geometry": geometry}]}))
            (source / "manifest.json").write_text(json.dumps({"request": request,
                "features": file_identity(source / "features.geojson"), "osm_base_timestamps": ["fixture"]}))
            manifest = json.loads(prepare_regional(source, DEFAULT_CONFIG, output, block_m=200).read_text())
            self.assertEqual(manifest["grid_shape"], [40, 40])
            with rasterio.open(output / "fields.tif") as raster:
                self.assertTrue((raster.read(raster.descriptions.index("greenery") + 1) > .999).all())
                self.assertEqual(raster.count, len(manifest["bands"]))
            with self.assertRaises(FileExistsError):
                prepare_regional(source, DEFAULT_CONFIG, output, block_m=200)
            self.assertFalse((output / "greenery.png").exists())

    def test_tiles_distinguish_coverage_and_zero_and_reject_stale_identity(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            grid = make_grid([374000, 6674000, 378000, 6678000], 10)
            values = np.ones((grid.height, grid.width), dtype=np.float32)
            write_raster(root / "fields.tif", {"greenery": values, "water": values*0,
                "context_support": values, "traffic_context_support": values*0}, grid)
            (root / "manifest.json").write_text(json.dumps({"dataset_id": "fixture", "method": "test",
                "bounds_epsg3067": [374000, 6674000, 378000, 6678000], "limitations": []}))
            bounds = transform_bounds("EPSG:3067", "EPSG:3857", 375900, 6675900, 376100, 6676100)
            z = 14
            x = int(((bounds[0]+bounds[2])/2 + 20037508.342789244) / (2*20037508.342789244) * 2**z)
            y = int((20037508.342789244 - (bounds[1]+bounds[3])/2) / (2*20037508.342789244) * 2**z)
            with patch.dict(os.environ, {"RUNMAP_ENVIRONMENT_TILE_DIRECTORY": str(root)}):
                self.assertIn("fixture", environment_tile_config()["tile_template"])
                def pixels(layer: str, tx: int = x, ty: int = y) -> np.ndarray:
                    response = environment_tile(1, "fixture", layer, z, tx, ty)
                    return np.asarray(Image.open(BytesIO(response.body)))
                self.assertGreater(pixels("greenery")[:, :, 3].max(), 0)
                self.assertEqual(pixels("water")[:, :, 3].max(), 0)
                self.assertGreater(pixels("support")[:, :, 3].max(), 0)
                self.assertEqual(pixels("greenery", 0, 0)[:, :, 3].max(), 0)
                for version, dataset, layer, zoom, tx, ty, status in [
                    (1, "stale", "greenery", z, x, y, 409), (2, "fixture", "greenery", z, x, y, 404),
                    (1, "fixture", "unknown", z, x, y, 404), (1, "fixture", "greenery", 30, 0, 0, 422),
                    (1, "fixture", "greenery", z, -1, 0, 422)]:
                    with self.assertRaises(HTTPException) as error:
                        environment_tile(version, dataset, layer, zoom, tx, ty)
                    self.assertEqual(error.exception.status_code, status)
            render_tile.cache_clear()

    def test_capital_area_request_matches_network_footprint(self) -> None:
        request = load_request(Path("config/environment/helsinki-capital-area.json"))
        network = json.loads(Path("config/routing-experiments/helsinki-capital-area.json").read_text())
        self.assertEqual(list(request.bounds_epsg3067), network["bounds_epsg3067"])
        self.assertGreaterEqual(request.halo_m, context_radius(load_config(DEFAULT_CONFIG)))


if __name__ == "__main__":
    unittest.main()

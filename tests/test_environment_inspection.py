"""Check source identity and boundary fidelity without terrain or routing artifacts."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from rasterio.warp import transform

from runmap.api.environment import boundary_coordinates, environment_config, environment_features


class EnvironmentInspectionTests(unittest.TestCase):
    def test_projected_core_preserves_closed_boundary_instead_of_envelope(self) -> None:
        bounds = [374000, 6674000, 380000, 6680000]
        ring = boundary_coordinates(bounds, True)
        self.assertEqual(len(ring), 81)
        self.assertEqual(ring[0], ring[-1])
        xs, ys = transform("EPSG:4326", "EPSG:3067", [p[0] for p in ring], [p[1] for p in ring])
        for x, y in zip(xs, ys):
            self.assertLess(min(abs(x - bounds[0]), abs(x - bounds[2]),
                                abs(y - bounds[1]), abs(y - bounds[3])), 0.001)

    def test_publishes_only_completed_source_and_rejects_stale_identity(self) -> None:
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"RUNMAP_ENVIRONMENT_DIRECTORY": temporary}):
            directory = Path(temporary)
            with self.assertRaises(HTTPException) as missing:
                environment_config()
            self.assertEqual(missing.exception.status_code, 404)
            manifest = {
                "request": {"bounds_epsg3067": [374000, 6674000, 380000, 6680000]},
                "features": {"sha256": "a" * 64},
                "query_bbox_wgs84_left_bottom_right_top": [24.7, 60.1, 24.9, 60.3],
            }
            (directory / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(HTTPException):
                environment_config()
            (directory / "features.geojson").write_text('{"type":"FeatureCollection","features":[]}')
            config = environment_config()
            self.assertEqual(config["manifest"], manifest)
            self.assertIn("dataset=" + "a" * 64, str(config["features_url"]))
            response = environment_features("a" * 64)
            self.assertEqual(response.path, directory / "features.geojson")
            self.assertEqual(response.headers["cache-control"], "no-cache")
            with self.assertRaises(HTTPException) as changed:
                environment_features("b" * 64)
            self.assertEqual(changed.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()

"""Synthetic checks for retained M4.1 terrain measurements."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform

from runmap.paths.graph import write_graph_outputs
from runmap.paths.measurements import (
    build_measurement_data, graph_digest, inspect_measurements, load_measurements,
)
from runmap.paths.terrain import TerrainSample, enrich_graph_terrain, _sample_gradients


def graph(points, tags=None):
    lon, lat = transform("EPSG:3067", "EPSG:4326", *zip(*points))
    return {"segments": [{"id": "s", "osm_way_id": "1", "from": "1", "to": "2",
                           "geometry": [list(p) for p in zip(lon, lat)],
                           "length_m": 123.0, "tags": tags or {},
                           "forward": True, "backward": True}],
            "arcs": [], "summary": {}}


def enrich(g, gradients, valid=None):
    def sample(paths, records):
        values = np.asarray([gradients(i, r) for i, r in enumerate(records)])
        return values, np.asarray([True if valid is None else valid(i) for i in range(len(records))])
    with patch("runmap.paths.terrain._sample_gradients", side_effect=sample):
        enrich_graph_terrain(g, [], 40, 10, manifest_id="test-manifest")
    document = {**g["_measurements"], "graph_digest": graph_digest(g)}
    return build_measurement_data(g, document)


class MeasurementTests(unittest.TestCase):
    def test_planes_cross_slope_and_curved_geometry(self):
        for magnitude, bearing, expected in [(0, 90, 0), (.1, 90, 4), (.1, 0, 0)]:
            g = graph([(375000, 6675100), (375040, 6675100)])
            index = enrich(g, lambda i, r: (magnitude, bearing))
            result = inspect_measurements(index, "s")
            self.assertAlmostEqual(result["ascent_m"], expected, places=5)
            self.assertAlmostEqual(result["intervals"][-1]["graph_end_m"], 123)
        g = graph([(375000, 6675100), (375020, 6675100), (375020, 6675120)])
        result = inspect_measurements(enrich(g, lambda i, r: (.1, 90)), "s")
        self.assertAlmostEqual(result["ascent_m"], 2, places=5)
        self.assertAlmostEqual(result["intervals"][-1]["grade_percent"], 0, places=5)

    def test_climb_descent_reversal_partial_and_gaps(self):
        g = graph([(375000, 6675100), (375040, 6675100)],
                  {"bridge": "yes", "tunnel": "yes", "highway": "steps", "area": "yes"})
        index = enrich(g, lambda i, r: (.1, 90 if r.start_m < 20 else 270),
                       lambda i: i != 1)
        forward = inspect_measurements(index, "s")
        reverse = inspect_measurements(index, "s", reverse=True)
        self.assertAlmostEqual(forward["ascent_m"], reverse["descent_m"])
        self.assertAlmostEqual(forward["descent_m"], reverse["ascent_m"])
        self.assertFalse(forward["totals_complete"])
        intervals = forward["intervals"]
        self.assertIsNone(intervals[1]["relative_end_m"])
        self.assertEqual(intervals[2]["relative_start_m"], 0)
        self.assertEqual(intervals[2]["profile_run"], 1)
        self.assertIn("missing_terrain", intervals[1]["quality_flags"])
        self.assertEqual(set(intervals[0]["quality_flags"]),
                         {"bridge", "tunnel", "stairs", "pedestrian_area_perimeter"})
        partial = inspect_measurements(index, "s", start_m=2, end_m=5)
        self.assertAlmostEqual(partial["ascent_m"], .3)
        self.assertEqual(partial["valid_length_fraction"], 1)
        self.assertEqual(reverse["intervals"][0]["interval_index"], len(intervals) - 1)

    def test_atomic_file_pair_and_mismatch(self):
        g = graph([(375000, 6675100), (375040, 6675100)])
        enrich(g, lambda i, r: (.1, 90))
        with TemporaryDirectory() as directory:
            paths = write_graph_outputs(g, directory)
            self.assertGreater(paths["measurements"].stat().st_size, 0)
            index = load_measurements(paths["graph"])
            self.assertEqual(index.document["method"]["manifest_id"], "test-manifest")
            changed = copy.deepcopy(g)
            changed["segments"][0]["length_m"] += 1
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                build_measurement_data(changed, index.document)
            old = json.loads(paths["graph"].read_text())
            enrich(g, lambda i, r: (.2, 90))
            write_graph_outputs(g, directory)
            self.assertTrue((Path(directory) / old["measurements"]["file"]).exists())

    def test_tile_seam_and_missing_samples(self):
        with TemporaryDirectory() as directory:
            paths = []
            for number, west in enumerate((375000, 375020)):
                path = Path(directory) / f"{number}.tif"
                with rasterio.open(path, "w", driver="GTiff", width=2, height=2,
                                   count=2, dtype="uint16", crs="EPSG:3067",
                                   transform=from_origin(west, 6675120, 10, 10), nodata=65535) as ds:
                    ds.write(np.full((2, 2), 1000, dtype="uint16"), 1)
                    ds.write(np.full((2, 2), 9000, dtype="uint16"), 2)
                paths.append(path)
            records = [TerrainSample(
                segment_id="s", east=x, north=6675105, length_m=10,
                direction_east=1, direction_north=0, start_m=0, end_m=10,
                graph_start_m=0, graph_end_m=10, start=(x - 5, 6675105), end=(x + 5, 6675105),
            ) for x in (375019, 375020, 375039, 375041)]
            values, valid = _sample_gradients(paths, records)
            self.assertEqual(valid.tolist(), [True, True, True, False])
            np.testing.assert_allclose(values[:3], [[.1, 90]] * 3)
            self.assertTrue(np.isnan(values[-1]).all())


if __name__ == "__main__":
    unittest.main()

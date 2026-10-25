"""Distance shells retain directed edge distances and bounded geographic detail."""

from dataclasses import replace
import unittest

import numpy as np
from shapely.geometry import MultiPolygon, Polygon, shape

from runmap.experiments.benchmark_distance_shells import shells_geojson
from runmap.paths.numeric_format import ChunkCounts, NumericChunk, NumericArray
from runmap.routing.distance_shells import (DistanceGrid, DistanceSamples, ShellSettings, build_distance_grid,
    distance_bands, sample_numeric_chunk)
from runmap.routing.numeric_search import NumericDistances, NumericEndpoint


def straight_chunk(*, backward: bool = True, allowed: bool = True) -> NumericChunk:
    arrays: dict[str, NumericArray] = {
        "node_ids": np.array([10, 20], dtype=np.int64),
        "segment_ids": np.array([7], dtype=np.int64),
        "arc_nodes": np.array([[0, 1], [1, 0]], dtype=np.int64),
        "arc_segments": np.array([0, 0], dtype=np.int64),
        "arc_lengths": np.array([1000., 1000.]),
        "arc_reverse": np.array([0, 1], dtype=np.uint8),
        "arc_allowed": np.array([[allowed], [allowed and backward]], dtype=np.uint8),
        "geometry_offsets": np.array([0, 3], dtype=np.int64),
        "geometry_xy": np.array([[375000., 6675000.], [375500., 6675000.], [376000., 6675000.]]),
    }
    return NumericChunk(ChunkCounts(2, 1, 2, 1, 3, 0), arrays)


def distances(nodes: list[int], values: list[float]) -> NumericDistances:
    return NumericDistances(np.array(nodes, dtype=np.int64), np.array(values, dtype=np.float64),
                            np.empty(0, dtype=np.int64), len(nodes), -1)


class DistanceShellTests(unittest.TestCase):
    def test_partial_start_and_one_way_distances(self) -> None:
        start = NumericEndpoint(segment_id=7, graph_offset_m=400, point=(375400, 6675000))
        for backward in (False, True):
            reached = distances([10, 20, -1], [400., 600., 0.]) if backward else distances([20, -1], [600., 0.])
            samples = sample_numeric_chunk(straight_chunk(backward=backward), reached, start, 0, 2000, 50)
            positions = samples.xy[:, 0] - 375000
            expected = np.abs(positions - 400)
            np.testing.assert_allclose(samples.distances, expected)
            if backward:
                self.assertLess(positions.min(), 400)
            else:
                self.assertGreaterEqual(positions.min(), 400)
            self.assertEqual(samples.distances.min(), 0)

    def test_both_endpoint_routes_and_budget_clipping(self) -> None:
        samples = sample_numeric_chunk(straight_chunk(), distances([10, 20], [100., 200.]),
                                       NumericEndpoint(node_id=10), 0, 550, 25)
        positions = samples.xy[:, 0] - 375000
        np.testing.assert_allclose(samples.distances, np.minimum(100 + positions, 200 + 1000 - positions))
        self.assertTrue(np.all(samples.distances <= 550))
        self.assertFalse(np.any((positions > 450) & (positions < 650)))

    def test_outer_support_samples_extend_known_edge_distances(self) -> None:
        samples = sample_numeric_chunk(straight_chunk(backward=False), distances([10], [0.]),
                                       NumericEndpoint(node_id=10), 0, 500, 25, support_m=200)
        self.assertGreater(samples.distances.max(), 500)
        self.assertLessEqual(samples.distances.max(), 700)
        np.testing.assert_allclose(samples.distances, samples.xy[:, 0] - 375000)

    def test_excluded_and_unreachable_edges_produce_no_samples(self) -> None:
        for chunk, reached in ((straight_chunk(allowed=False), distances([10, 20], [0., 1000.])),
                                (straight_chunk(), distances([], []))):
            samples = sample_numeric_chunk(chunk, reached, NumericEndpoint(node_id=10), 0, 1000, 25)
            self.assertEqual(samples.xy.shape, (0, 2))

    def test_bent_geometry_uses_chainage_and_not_endpoint_chord(self) -> None:
        chunk = straight_chunk(backward=False)
        arrays = dict(chunk.arrays)
        arrays["geometry_xy"] = np.array([[0., 0.], [0., 500.], [500., 500.]])
        samples = sample_numeric_chunk(replace(chunk, arrays=arrays), distances([10], [0.]),
                                       NumericEndpoint(node_id=10), 0, 1000, 50)
        np.testing.assert_allclose(samples.distances, samples.xy[:, 0] + samples.xy[:, 1])
        self.assertTrue(np.any((samples.xy[:, 0] == 0) & (samples.xy[:, 1] == 250)))

    def test_grid_radius_and_cell_budget(self) -> None:
        settings = ShellSettings(cell_m=25, radius_m=50)
        grid = build_distance_grid(DistanceSamples(np.array([[0., 0.], [100., 0.]]), np.array([0., 100.])), settings)
        self.assertTrue(np.isnan(grid.values[0, 0]))
        self.assertEqual(grid.values[np.where(grid.y == 0)[0][0], np.where(grid.x == 0)[0][0]], 0)
        with self.assertRaisesRegex(ValueError, "cell budget"):
            build_distance_grid(DistanceSamples(np.array([[0., 0.], [1e6, 1e6]]), np.array([0., 100.])), settings)
        for bad in (ShellSettings(cell_m=0), ShellSettings(radius_m=float("nan"))):
            with self.assertRaises(ValueError):
                build_distance_grid(DistanceSamples(np.array([[0., 0.]]), np.array([0.])), bad)

    def test_bands_preserve_holes_and_partition_without_overlap(self) -> None:
        x, y = np.arange(375000., 376025., 25), np.arange(6675000., 6676025., 25)
        xx, yy = np.meshgrid(x, y)
        values = np.hypot(xx - x.mean(), yy - y.mean()) * 2
        values[18:23, 18:23] = np.nan
        bands = distance_bands(DistanceGrid(x, y, values), 1250, ShellSettings())
        self.assertEqual([(band.lower_m, band.upper_m) for band in bands], [(0, 500), (500, 1000), (1000, 1250)])
        geometries = [MultiPolygon([Polygon(rings[0], rings[1:]) for rings in band.polygons]) for band in bands]
        self.assertTrue(all(geometry.is_valid for geometry in geometries))
        self.assertGreater(len(geometries[0].geoms[0].interiors), 0)
        for first, second in zip(geometries[:-1], geometries[1:]):
            self.assertLess(first.intersection(second).area, 1e-5)
        geojson, vertices = shells_geojson(bands)
        self.assertGreater(vertices, 0)
        self.assertEqual(len(geojson["features"]), 3)
        self.assertTrue(all(shape(feature["geometry"]).is_valid for feature in geojson["features"]))

    def test_exact_threshold_extrema_do_not_create_degenerate_rings(self) -> None:
        coordinates = np.arange(5, dtype=np.float64)
        values = np.full((5, 5), 1000.)
        values[1, 1], values[3, 3] = 0, 500
        bands = distance_bands(DistanceGrid(coordinates, coordinates, values), 1000, ShellSettings())
        for band in bands:
            for rings in band.polygons:
                self.assertTrue(Polygon(rings[0], rings[1:]).is_valid)


if __name__ == "__main__":
    unittest.main()

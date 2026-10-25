"""Mesh reduction retains distance boundaries and coarse interior anchors."""

import unittest

import numpy as np

from runmap.experiments.benchmark_distance_mesh import mesh_sites
from runmap.routing.distance_shells import DistanceSamples, ShellSettings
from runmap.routing.distance_mesh import DistanceMesh, MeshUnavailableError, build_distance_mesh, triangulated_bands
from shapely.geometry import MultiPolygon, Polygon


class DistanceMeshTests(unittest.TestCase):
    def test_duplicate_coordinates_keep_shortest_distance(self) -> None:
        samples = DistanceSamples(np.array([[0., 0.], [0., 0.], [100., 100.]]), np.array([50., 10., 100.]))
        result = mesh_sites(samples, None)
        np.testing.assert_array_equal(result.distances, [10, 100])

    def test_cell_reduction_retains_minimum_distance(self) -> None:
        samples = DistanceSamples(np.array([[0., 0.], [10., 10.], [51., 0.]]), np.array([50., 10., 100.]))
        result = mesh_sites(samples, 50)
        np.testing.assert_array_equal(result.distances, [10, 100])

    def test_boundary_reduction_keeps_near_sites_and_coarse_extremes(self) -> None:
        samples = DistanceSamples(np.array([[0., 0.], [55., 0.], [110., 0.], [165., 0.],
                                            [200., 0.], [255., 0.], [310., 0.]]),
                                  np.array([200., 220., 230., 240., 480., 510., 520.]))
        result = mesh_sites(samples, 50, boundary_window_m=50)
        self.assertEqual(set(result.distances), {200, 240, 480, 510, 520})

    def test_final_partial_band_boundary_is_retained(self) -> None:
        samples = DistanceSamples(np.array([[0., 0.], [55., 0.], [110., 0.], [165., 0.]]),
                                  np.array([710., 720., 730., 740.]))
        result = mesh_sites(samples, 50, boundary_window_m=50, maximum_m=750)
        self.assertEqual(len(result.distances), 4)

    def test_triangle_clipping_partitions_mesh_with_shared_boundaries(self) -> None:
        mesh = DistanceMesh(np.array([[0., 0.], [1000., 0.], [1000., 1000.], [0., 1000.]]),
                            np.array([0., 1000., 1000., 0.]), np.array([[0, 1, 2], [0, 2, 3]]))
        bands = triangulated_bands(mesh, 1000, ShellSettings())
        shapes = [MultiPolygon([Polygon(rings[0], rings[1:]) for rings in band.polygons]) for band in bands]
        self.assertTrue(all(geometry.is_valid for geometry in shapes))
        self.assertEqual([geometry.area for geometry in shapes], [500000, 500000])
        self.assertEqual(shapes[0].intersection(shapes[1]).area, 0)
        self.assertEqual(shapes[0].union(shapes[1]).area, 1000000)

    def test_masked_triangle_hole_survives_band_assembly(self) -> None:
        x, y = np.meshgrid(np.arange(4, dtype=np.float64), np.arange(4, dtype=np.float64))
        points = np.column_stack((x.ravel(), y.ravel()))
        triangles = []
        for row in range(3):
            for column in range(3):
                if (row, column) == (1, 1):
                    continue
                start = row * 4 + column
                triangles.extend(((start, start + 1, start + 5), (start, start + 5, start + 4)))
        bands = triangulated_bands(DistanceMesh(points, np.full(16, 100.), np.array(triangles)),
                                   500, ShellSettings())
        polygon = Polygon(bands[0].polygons[0][0], bands[0].polygons[0][1:])
        self.assertTrue(polygon.is_valid)
        self.assertEqual(len(polygon.interiors), 1)
        self.assertEqual(polygon.area, 8)

    def test_collinear_sites_report_grid_fallback_condition(self) -> None:
        samples = DistanceSamples(np.array([[0., 0.], [100., 0.], [200., 0.]]), np.array([0., 100., 200.]))
        with self.assertRaises(MeshUnavailableError):
            build_distance_mesh(samples, 500, ShellSettings())

    def test_constant_threshold_triangle_belongs_to_only_one_band(self) -> None:
        mesh = DistanceMesh(np.array([[0., 0.], [100., 0.], [0., 100.]]), np.full(3, 500.), np.array([[0, 1, 2]]))
        bands = triangulated_bands(mesh, 1000, ShellSettings())
        self.assertTrue(bands[0].polygons)
        self.assertFalse(bands[1].polygons)


if __name__ == "__main__":
    unittest.main()

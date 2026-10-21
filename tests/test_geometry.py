"""Lossless packed graph geometry and shared endpoint overlays."""

from copy import deepcopy
import json
from tempfile import TemporaryDirectory
import unittest

from test_routing import graph_document
from runmap.paths.graph import write_graph_outputs
from runmap.paths.measurements import graph_digest
from runmap.paths.exploration import Snap, split_start_graph
from runmap.routing.coordinates import PackedLine, coordinate_json
from runmap.routing.geometry import (indexed_node_point, indexed_segment_points,
                                     prepare_projected_geometry, projected_points)
from runmap.routing.search import apply_profile, build_graph_index, route_coordinates


class PackedGeometryTests(unittest.TestCase):
    def test_geometry_and_published_json_survive_packing_exactly(self) -> None:
        graph = graph_document(
            [("a", 24, 60.0), ("b", 24.02, 60)],
            [("ab", "a", "b", 1000, 0, {"highway": "path"})], start="a")
        graph["segments"][0]["geometry"] = [[24, 60.0], [24.01, 60.005], [24.01, 60.005], [24.02, 60]]
        graph["segments"][0]["osm_way_id"] = "1"
        original = deepcopy(graph)
        digest = graph_digest(graph)
        encoded = json.dumps(graph, sort_keys=True, allow_nan=False)
        source_coordinates = graph["segments"][0]["geometry"]
        index = build_graph_index(graph)
        line = graph["segments"][0]["geometry"]
        self.assertIsInstance(line, PackedLine)
        self.assertEqual(list(line), original["segments"][0]["geometry"])
        self.assertEqual(json.dumps(graph, sort_keys=True, allow_nan=False, default=coordinate_json), encoded)
        self.assertEqual(graph_digest(graph), digest)
        for selection in (slice(None), slice(None, None, -1), slice(1, 3), slice(3, 0, -2)):
            self.assertEqual(line[selection], original["segments"][0]["geometry"][selection])
        self.assertEqual(line[-1], original["segments"][0]["geometry"][-1])
        for position in (-5, 4):
            with self.assertRaises(IndexError):
                _ = line[position]
        detached = line[0]
        detached[0] = 0
        self.assertEqual(line[0][0], 24)
        source_coordinates[0][0] = 0
        self.assertEqual(line[0][0], 24)
        self.assertIs(deepcopy(line), line)
        self.assertEqual(indexed_segment_points(index.projected, 0),
                         tuple(projected_points(original["segments"][0]["geometry"])))
        coordinates = route_coordinates(index, [next(iter(index.arcs))])
        coordinates[0][0] = 0
        self.assertEqual(line[0][0], 24)
        # Exporting an indexed graph must retain the original artifact representation.
        graph.setdefault("summary", {})
        with TemporaryDirectory() as directory:
            paths = write_graph_outputs(graph, directory)
            loaded = json.loads(paths["graph"].read_text())
            self.assertEqual(loaded["segments"][0]["geometry"], list(line))
            self.assertIs(type(loaded["segments"][0]["geometry"][0][0]), int)

    def test_read_only_arrays_and_policy_overlay_sharing(self) -> None:
        graph = graph_document(
            [("a", 24.8, 60.2), ("b", 24.81, 60.2)],
            [("ab", "a", "b", 550, 0, {"highway": "path"})], start="a")
        index = build_graph_index(graph)
        geometry = index.projected
        policy = apply_profile(index, index.profile)
        self.assertIs(policy.projected, geometry)
        overlay, _ = split_start_graph(index, Snap("ab", 100, 100, 24.802, 60.2, 0))
        self.assertIs(overlay.projected.node_points, geometry.node_points)
        self.assertIs(overlay.projected.segments, geometry.segments)
        self.assertIs(overlay.projected.wgs84, geometry.wgs84)
        self.assertIs(geometry.segments.offsets, geometry.wgs84.offsets)
        self.assertEqual(len(geometry.extra_node_points), 0)
        self.assertEqual(len(overlay.projected.extra_node_points), 1)
        for values in (geometry.node_points, geometry.segments.points, geometry.wgs84.points):
            self.assertTrue(values.readonly)
            with self.assertRaises(TypeError):
                values[0, 0] = 0
        self.assertTrue(geometry.wgs84.offsets.readonly)
        with self.assertRaises(TypeError):
            geometry.wgs84.offsets[0] = 1
        for reader in (indexed_node_point, indexed_segment_points):
            for position in (-1, 10):
                with self.assertRaises(IndexError):
                    reader(geometry, position)
        other = build_graph_index(deepcopy(graph))
        with self.assertRaisesRegex(ValueError, "share the base"):
            prepare_projected_geometry({}, {}, (), other.integer, geometry)

    def test_empty_graph_has_empty_packed_arrays(self) -> None:
        index = build_graph_index({"nodes": [], "segments": [], "arcs": []})
        self.assertEqual(index.projected.node_points.shape, (0, 2))
        self.assertEqual(index.projected.segments.points.shape, (0, 2))
        self.assertEqual(list(index.projected.wgs84.offsets), [0])

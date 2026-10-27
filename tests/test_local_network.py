"""Search windows share stable IDs while retaining only local numeric buffers."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from rasterio.warp import transform

from runmap.api.paths import load_path_data, path_policy_index
from runmap.paths.database import export_network_database, open_network_database
from runmap.paths.exploration import Snap, segment_reason
from runmap.routing_reference.exploration import split_start_graph
from runmap.paths.graph import write_graph_outputs
from runmap.paths.local_network import load_local_network
from runmap.paths.measurements import segment_measurements
from runmap.routing.hills import indexed_traversal_hills
from runmap.routing.profiles import exploration_profile
from runmap.routing_reference.search import shortest_distances, shortest_path
from test_database_features import BOUNDS, driveway_graph
from test_measurements import enrich
from test_path_database import publish_fixture


class LocalNetworkTests(unittest.TestCase):
    def test_crossing_segments_dense_ids_frontier_and_expansion(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, samples = publish_fixture(root)
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                local = load_local_network(connection, (375040, 6674990, 375060, 6675010))
                index = local.routing.integer
                self.assertEqual(index.segment_ids, ("sb",))
                self.assertEqual(dict(index.segment_indices), {"sb": 0})
                self.assertEqual(set(index.node_ids), {"s", "b"})
                self.assertEqual(set(index.arc_ids), {"sb:f", "sb:b"})
                self.assertEqual(local.frontier_nodes, {"b"})
                self.assertEqual(local.frontier_indices, {index.node_indices["b"]})
                self.assertNotIn("s", local.frontier_nodes)  # A true physical dead end.
                self.assertEqual(segment_measurements(local.measurements, "sb"), samples["sb"])
                self.assertIs(local.measurements.segment_ids, index.segment_ids)
                self.assertIs(local.hills.index, index)
                self.assertEqual(shortest_path(local.routing, "s", "b").cost, 100)
                expanded = load_local_network(connection, (374990, 6674990, 375210, 6675010))
                self.assertFalse(expanded.frontier_nodes)
                self.assertEqual(shortest_path(expanded.routing, "s", "c").cost, 200)
                self.assertEqual(expanded.metadata.dataset_id, local.metadata.dataset_id)
                self.assertNotIn("de", expanded.routing.segments)
                # A separately queried stable segment gets its own dense zero index.
                other = load_local_network(connection, (380040, 6674990, 380060, 6675010))
                self.assertEqual(dict(other.routing.integer.segment_indices), {"de": 0})

    def test_profiles_distances_partial_start_and_hills_match_full_graph(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            expected = load_path_data(source)
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                for surface in ("all", "surfaced"):
                    for stairs in (False, True):
                        reference = path_policy_index(expected, surface, stairs)
                        local = load_local_network(connection, BOUNDS, exploration_profile(surface, stairs))
                        self.assertEqual(shortest_distances(local.routing, "s"), shortest_distances(reference, "s"))
                        for key in reference.segments:
                            self.assertEqual(segment_reason(local.routing, key), segment_reason(reference, key))
                            self.assertEqual(local.routing.direction_cache[key], reference.direction_cache[key])
                        for number, key in enumerate(local.routing.integer.segment_ids):
                            ref_number = expected.hills.index.segment_indices[key]
                            for reverse in (False, True):
                                for start, end in ((0, 100), (5, 95), (21.5, 38.5)):
                                    self.assertEqual(indexed_traversal_hills(local.hills, number, start, end, reverse),
                                                     indexed_traversal_hills(expected.hills, ref_number, start, end, reverse))
                        self.assertEqual(local.routing.projected.distance_scale, expected.routing.projected.distance_scale)
                        for array in (local.measurements.values, local.hills.features, local.hills.totals):
                            self.assertFalse(array.flags.writeable)
                local = load_local_network(connection, (375040, 6674990, 375060, 6675010))
                lon, lat = transform("EPSG:3067", "EPSG:4326", [375050], [6675000])
                snap = Snap("sb", 50, 50, lon[0], lat[0], 0)
                left, start = split_start_graph(local.routing, snap)
                right, ref_start = split_start_graph(expected.routing, snap)
                self.assertEqual(shortest_path(left, start, "b"), shortest_path(right, ref_start, "b"))
                self.assertEqual(shortest_distances(left, start, 40), shortest_distances(right, ref_start, 40))

    def test_global_classification_is_retained_without_preparation_or_json(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph = driveway_graph()
            enrich(graph, lambda i, record: (0, 90))
            source = write_graph_outputs(graph, root)["graph"]
            reference = load_path_data(source)
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with patch.object(Path, "read_text", side_effect=AssertionError("JSON read")), \
                    patch("runmap.routing.graph.classify_segments", side_effect=AssertionError("reclassified")), \
                    patch("runmap.routing.graph._boundary_blocked_segments", side_effect=AssertionError("recomputed")), \
                    closing(open_network_database(destination)) as connection:
                local = load_local_network(connection, (375071, 6674999, 375079, 6675001))
                self.assertEqual(set(local.routing.segments), {"xy"})
                self.assertEqual(local.building_access, {"xy": reference.building_access["xy"]})
                self.assertEqual(local.building_access["xy"].attachment_node, "a")
                self.assertNotIn("a", local.routing.nodes)
                self.assertEqual(local.frontier_nodes, {"x"})
                self.assertFalse(local.coverage_limited_segments)

    def test_boundaries_one_way_and_node_access_survive_local_loading(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, graph, _ = publish_fixture(root, terrain=False)
            lon, lat = transform("EPSG:3067", "EPSG:4326", [375050, 375050], [6674900, 6675100])
            graph["route_boundaries"] = [{"id": "wall", "osm_way_id": "wall", "ref": "wall", "name": "wall",
                                           "geometry": [list(point) for point in zip(lon, lat)]}]
            graph["nodes"][-1]["foot"] = "no"
            graph["segments"][1]["tags"] = {"highway": "path", "oneway:foot": "-1"}
            source.write_text(json.dumps(graph))
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                local = load_local_network(connection, BOUNDS)
                self.assertEqual(local.routing.boundary_blocked_segments, {"sb"})
                self.assertEqual(segment_reason(local.routing, "de"), "node:e:foot:no")
                self.assertIsNone(shortest_path(local.routing, "b", "c"))
                self.assertEqual(shortest_path(local.routing, "c", "b").cost, 100)
                self.assertIsNone(shortest_path(local.routing, "s", "b"))
                self.assertFalse(local.frontier_nodes)
                self.assertIsNone(local.measurements)
                self.assertIsNone(local.hills)
                narrow = load_local_network(connection, (375041, 6674999, 375049, 6675001))
                self.assertEqual(narrow.frontier_nodes, {"b"})
                self.assertEqual(narrow.routing.boundary_blocked_segments, {"sb"})

    def test_empty_measured_window_and_unknown_profile(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                local = load_local_network(connection, (0, 0, 1, 1))
                self.assertFalse(local.routing.integer.segment_ids)
                self.assertFalse(local.frontier_nodes)
                self.assertEqual(local.measurements.values.shape, (0, 6))
                self.assertEqual(local.hills.features.shape, (0, 2, 7))
                self.assertTrue(np.array_equal(local.hills.interval_offsets, [0]))
                with self.assertRaisesRegex(ValueError, "persisted routing profile"):
                    load_local_network(connection, BOUNDS, {"id": "unsupported"})

    def test_empty_samples_keep_prepared_fallback_hills(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, graph, _ = publish_fixture(root)
            samples = root / "measurements.json"
            document = json.loads(samples.read_text())
            document["segments"]["sb"] = []
            payload = json.dumps(document).encode()
            samples.write_bytes(payload)
            graph["measurements"]["sha256"] = hashlib.sha256(payload).hexdigest()
            source.write_text(json.dumps(graph))
            reference = load_path_data(source)
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                local = load_local_network(connection, (375041, 6674999, 375049, 6675001))
                self.assertEqual(segment_measurements(local.measurements, "sb"), [])
                self.assertEqual(local.hills.features.shape, (1, 2, 7))
                self.assertEqual(indexed_traversal_hills(local.hills, 0, 20, 70, False),
                                 indexed_traversal_hills(reference.hills, 0, 20, 70, False))

    def test_source_coverage_edges_are_independent_of_unloaded_connections(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph = driveway_graph()
            graph["experiment"]["bounds_epsg3067"] = [374000, 6674000, 375100, 6676000]
            enrich(graph, lambda i, record: (0, 90))
            source = write_graph_outputs(graph, root)["graph"]
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                local = load_local_network(connection, (375071, 6674999, 375079, 6675001))
                self.assertEqual(local.frontier_nodes, {"x"})
                self.assertEqual(local.coverage_limited_segments, {"xy"})
                expanded = load_local_network(connection, BOUNDS)
                self.assertFalse(expanded.frontier_nodes)
                self.assertIn("xy", expanded.coverage_limited_segments)


if __name__ == "__main__":
    unittest.main()

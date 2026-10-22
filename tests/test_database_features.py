"""Prepared SQLite features match the full-network reference after spatial selection."""

from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from rasterio.warp import transform

from runmap.api.paths import load_path_data, path_policy_index
from runmap.paths.coverage import near_coverage_boundary
from runmap.paths.database import (export_network_database, network_metadata, open_network_database,
                                   query_display_intervals, query_network, query_projected_nodes,
                                   query_segment_features)
from runmap.paths.exploration import segment_reason
from runmap.paths.graph import write_graph_outputs
from runmap.paths.measurements import segment_measurements
from runmap.routing.geometry import indexed_node_point, indexed_segment_points
from runmap.routing.hill_types import HillData
from runmap.routing.hills import indexed_traversal_hills
from runmap.routing.types import GraphDocument
from test_dead_ends import CORE, graph_fixture
from test_measurements import enrich
from test_path_database import publish_fixture


BOUNDS = (374000.0, 6674000.0, 381000.0, 6676000.0)


def driveway_graph() -> GraphDocument:
    graph = graph_fixture(CORE + [("ax", "a", "x", "driveway"), ("xy", "x", "y", "driveway")])
    points = {"a": (375000, 6675000), "b": (375030, 6675000), "c": (375015, 6675030),
              "x": (375060, 6675000), "y": (375090, 6675000)}
    lon, lat = transform("EPSG:3067", "EPSG:4326", *zip(*points.values()))
    coordinates = dict(zip(points, zip(lon, lat)))
    for node in graph["nodes"]:
        node["longitude"], node["latitude"] = coordinates[node["id"]]
    for segment in graph["segments"]:
        segment["geometry"] = [list(coordinates[segment["from"]]), list(coordinates[segment["to"]])]
        segment["forward"] = segment["backward"] = True
    graph["summary"] = {}
    graph["experiment"] = {"bounds_epsg3067": [374000, 6674000, 376000, 6676000]}
    return graph


class PreparedDatabaseTests(unittest.TestCase):
    def test_display_queries_match_measurements_thresholds_and_source_boundary(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            expected = load_path_data(source)
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                actual = query_display_intervals(connection, BOUNDS, hide_building_access=False)
                self.assertEqual(len(actual), len(expected.features))
                for stored, reference in zip(actual, expected.features):
                    segment_id = expected.measurements.segment_ids[reference.segment]
                    interval = segment_measurements(expected.measurements, segment_id)[reference.interval_index]
                    self.assertEqual(stored.measurement, interval)
                    self.assertEqual(stored.flat_min_grade_50m, reference.flat_min_grade_50m)
                    self.assertEqual(stored.surface_class, reference.surface_class)
                    self.assertEqual(stored.coverage_limited, near_coverage_boundary(expected.coverage, interval["geometry"]))
                # A narrow query selects individual intervals rather than every sample on the segment.
                narrow = query_display_intervals(connection, (375041, 6674999, 375049, 6675001))
                self.assertEqual(len(narrow), 1)
                self.assertEqual(narrow[0].measurement["segment_id"], "sb")
                self.assertEqual(len(query_network(connection, (375041, 6674999, 375049, 6675001)).measurements["sb"]), 10)
                self.assertEqual(query_display_intervals(connection, (0, 0, 1, 1)), ())
                self.assertEqual(query_segment_features(connection, (0, 0, 1, 1)), {})
                self.assertEqual(query_projected_nodes(connection, (0, 0, 1, 1)), {})

    def test_directional_policies_projection_and_partial_hills_match_reference(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            expected = load_path_data(source)
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                metadata = network_metadata(connection)
                self.assertEqual(metadata.distance_scale, expected.routing.projected.distance_scale)
                self.assertEqual(metadata.hill_valid_fraction, expected.hills.valid_fraction)
                self.assertEqual(metadata.hill_trusted_fraction, expected.hills.trusted_fraction)
                self.assertEqual(metadata.models.hill_model, expected.hills.model)
                features = query_segment_features(connection, BOUNDS)
                nodes = query_projected_nodes(connection, BOUNDS)
                for number, node_id in enumerate(expected.routing.integer.node_ids):
                    self.assertEqual(nodes[node_id], indexed_node_point(expected.routing.projected, number))
                for number, segment_id in enumerate(expected.measurements.segment_ids):
                    stored = features[segment_id]
                    self.assertEqual(stored.projected_geometry, indexed_segment_points(expected.routing.projected, number))
                    policies = {item.policy_id: item for item in stored.policies}
                    for surface in ("all", "surfaced"):
                        for stairs in (False, True):
                            index = path_policy_index(expected, surface, stairs)
                            policy = policies[index.profile["id"]]
                            self.assertEqual(policy.reason, segment_reason(index, segment_id))
                            self.assertEqual(policy.forward, True in index.direction_cache[segment_id])
                            self.assertEqual(policy.backward, False in index.direction_cache[segment_id])
                hills = expected.hills
                intervals = [interval for stored in features.values() for interval in stored.hill_intervals]
                restored = HillData(
                    hills.graph, hills.graph_digest, metadata.models.hill_model, hills.index, hills.interval_offsets,
                    np.array([(item.graph_start_m, item.graph_end_m) for item in intervals]),
                    np.array([[list(asdict(item.forward).values()), list(asdict(item.backward).values())] for item in intervals]),
                    np.array([[list(asdict(stored.forward_hills).values()), list(asdict(stored.backward_hills).values())]
                              for stored in features.values()]), metadata.hill_valid_fraction, metadata.hill_trusted_fraction,
                )
                for segment in range(3):
                    for reverse in (False, True):
                        for start, end in ((0, 100), (5, 95), (21.5, 38.5)):
                            self.assertEqual(indexed_traversal_hills(restored, segment, start, end, reverse),
                                             indexed_traversal_hills(hills, segment, start, end, reverse))

    def test_building_access_keeps_full_network_context_in_a_small_window(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph = driveway_graph()
            enrich(graph, lambda i, record: (0, 90))
            outputs = write_graph_outputs(graph, root)
            expected = load_path_data(outputs["graph"])
            self.assertEqual(set(expected.building_access), {"ax", "xy"})
            destination = root / "network.sqlite"
            export_network_database(outputs["graph"], destination)
            # Readers must not rerun any preparation on the cropped graph or reread JSON.
            with patch("runmap.paths.database.prepare_network", side_effect=AssertionError("recomputed")), \
                    patch.object(Path, "read_text", side_effect=AssertionError("read source JSON")), \
                    closing(open_network_database(destination)) as connection:
                bounds = (375071, 6674999, 375079, 6675001)
                features = query_segment_features(connection, bounds)
                self.assertEqual(set(features), {"xy"})
                classification = features["xy"].building_access
                self.assertEqual(classification, expected.building_access["xy"])
                self.assertEqual(classification.attachment_node, "a")
                self.assertEqual(classification.branch_length_m, 60)
                self.assertEqual(query_display_intervals(connection, bounds), ())
                visible = query_display_intervals(connection, bounds, hide_building_access=False)
                self.assertTrue(visible)
                self.assertTrue(all(item.building_access == classification for item in visible))
                self.assertTrue(all(not item.coverage_limited for item in visible))

    def test_boundaries_and_node_access_are_persisted_separately_from_frontier(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, graph, _ = publish_fixture(root, terrain=False)
            lon, lat = transform("EPSG:3067", "EPSG:4326", [375050, 375050], [6674900, 6675100])
            graph["route_boundaries"] = [{"id": "wall", "osm_way_id": "wall", "ref": "wall", "name": "wall",
                                           "geometry": [list(point) for point in zip(lon, lat)]}]
            graph["nodes"][-1]["foot"] = "no"
            graph["segments"][0]["tags"]["surface"] = "asphalt"
            graph["segments"][-1]["tags"]["surface"] = "asphalt"
            graph["segments"][1]["tags"] = {"highway": "path", "surface": "asphalt", "oneway:foot": "-1"}
            source.write_text(json.dumps(graph))
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                features = query_segment_features(connection, BOUNDS)
                self.assertTrue(features["sb"].boundary_blocked)
                self.assertTrue(all(item.reason == "prepared_route_boundary" for item in features["sb"].policies))
                self.assertTrue(all(item.reason == "node:e:foot:no" for item in features["de"].policies))
                self.assertTrue(all(item.reason is None and not item.forward and item.backward for item in features["bc"].policies))
                self.assertIsNone(features["sb"].forward_hills)
                self.assertEqual(features["sb"].hill_intervals, ())
                self.assertIsNone(network_metadata(connection).models.hill_model)

    def test_model_changes_identity_corruption_and_schema_one_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            for name, key, value in (("models", "models", None), ("identity", "feature_identity", "wrong"),
                                      ("schema", "schema", 1)):
                destination = root / (name + ".sqlite")
                export_network_database(source, destination)
                with closing(sqlite3.connect(destination)) as writer, writer:
                    if key == "models":
                        models = json.loads(writer.execute("SELECT value FROM metadata WHERE key = 'models'").fetchone()[0])
                        models["flat_minimum_length_m"] = 75
                        writer.execute("UPDATE metadata SET value = ? WHERE key = 'models'", (json.dumps(models),))
                    elif key == "schema":
                        writer.execute("PRAGMA user_version = 1")
                    else:
                        writer.execute("UPDATE metadata SET value = ? WHERE key = ?", (json.dumps(value), key))
                with self.assertRaisesRegex(ValueError, "models changed|identity mismatch|schema"):
                    open_network_database(destination)

    def test_empty_sample_segment_keeps_fallback_hills_and_empty_measurement_list(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, graph, _ = publish_fixture(root)
            document = json.loads((root / "measurements.json").read_text())
            document["segments"]["sb"] = []
            payload = json.dumps(document).encode()
            (root / "measurements.json").write_bytes(payload)
            graph["measurements"]["sha256"] = hashlib.sha256(payload).hexdigest()
            source.write_text(json.dumps(graph))
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                bounds = (375041, 6674999, 375049, 6675001)
                self.assertEqual(query_network(connection, bounds).measurements, {"sb": []})
                self.assertEqual(query_display_intervals(connection, bounds), ())
                stored = query_segment_features(connection, bounds)["sb"]
                self.assertEqual(len(stored.hill_intervals), 1)
                self.assertGreater(stored.forward_hills.fallback_length_m, 99)
                self.assertEqual(stored.forward_hills.valid_length_m, 0)
                self.assertEqual(stored.forward_hills, stored.backward_hills)


if __name__ == "__main__":
    unittest.main()

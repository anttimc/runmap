import copy
import json
import tempfile
import unittest

from rasterio.warp import transform

from runmap.routing.atomic_regions import build_atomic_region_inventory
from runmap.routing.face_search import _polygon_area, ordered_boundary
from runmap.routing.microcycles import (
    build_microcycle_partition, verify_microcycle_partition, write_microcycle_outputs,
)
from runmap.routing.search import GraphIndex
from tests.test_atomic_regions import grid_graph
from tests.test_face_search import two_square_graph


def grouped_grid(groups, floor):
    index, topology = grid_graph(3, 3)
    embedding = topology["face_embedding"]
    for face in embedding["faces"]:
        xs, ys = transform("EPSG:4326", "EPSG:3067", *zip(*face["geometry"]))
        face["area_m2"] = _polygon_area(list(zip(xs, ys))[:-1])
    for interface in embedding["face_adjacency"]:
        if any(set(interface["faces"]) <= group for group in groups):
            index.segments[interface["segment_id"]]["tags"]["surface"] = "dirt"
    inventory = build_atomic_region_inventory(index, topology,
                    {"edge_profile": {"forbidden_surfaces": ["dirt"]}},
                    {"minimum_convexity_ratio": floor, "fill_enclosed_holes": False})
    return index, inventory


class MicrocyclePartitionTests(unittest.TestCase):
    def test_accepted_atomic_regions_are_not_merged_to_hit_a_length(self):
        index, topology = two_square_graph()
        inventory = build_atomic_region_inventory(index, topology)
        result = build_microcycle_partition(index, inventory)
        self.assertEqual(result["summary"]["merged_count"], 0)
        self.assertEqual(result["summary"]["classification_counts"]["accepted"], 2)
        self.assertEqual(len(result["shared_interfaces"]), 1)
        self.assertTrue(verify_microcycle_partition(index, result))

    def test_concave_region_chooses_the_best_shape_repair_neighbor(self):
        l_shape = {"cell-0-0", "cell-1-0", "cell-2-0", "cell-0-1", "cell-0-2"}
        index, inventory = grouped_grid([l_shape], 0.8)
        poor = next(region for region in inventory["regions"] if set(region["member_face_ids"]) == l_shape)
        self.assertLess(poor["shape_metrics"]["convexity_ratio"], 0.8)
        result = build_microcycle_partition(index, inventory)
        self.assertEqual(result["summary"]["merged_count"], 1)
        repaired = next(region for region in result["regions"] if region["children"])
        self.assertEqual(set(repaired["member_face_ids"]), l_shape | {"cell-1-1"})
        self.assertEqual(repaired["status"], "accepted")
        self.assertGreater(repaired["shape_metrics"]["convexity_ratio"], 0.8)
        self.assertEqual(repaired["repair_sequence"], [repaired["id"]])
        self.assertTrue(verify_microcycle_partition(index, result))

    def test_unknown_neighbor_becomes_a_mixed_rectangle_with_exact_provenance(self):
        index, topology = two_square_graph()
        index.segments["ab"]["tags"]["surface"] = "asphalt"
        inventory = build_atomic_region_inventory(index, topology,
                        {"edge_profile": {"require_known_surface_on_boundary": True}})
        original = copy.deepcopy(inventory)
        result = build_microcycle_partition(index, inventory)
        self.assertEqual(inventory, original)
        self.assertEqual(len(result["regions"]), 1)
        parent = result["regions"][0]
        self.assertEqual(parent["status"], "accepted")
        self.assertEqual(parent["perimeter_m"], 600)
        self.assertEqual(parent["merge_interface"], {"segment_ids": ["be"], "length_m": 100})
        self.assertEqual(parent["surface_metrics"]["unknown_surface_fraction"], 0.833333)
        self.assertEqual(set(parent["member_face_ids"]), {"left", "right"})
        self.assertEqual(len(parent["atomic_region_ids"]), 2)
        self.assertEqual(len(result["nodes"]), 3)
        self.assertTrue(all(not node["active"] and node["parent"] == parent["id"]
                            for node in result["nodes"] if node["id"] != parent["id"]))
        self.assertEqual(result["repair_events"][0]["measurements"]["perimeter_identity_error_m"], 0)

    def test_unknown_only_merges_are_not_progress(self):
        index, topology = two_square_graph()
        inventory = build_atomic_region_inventory(index, topology,
                        {"edge_profile": {"require_known_surface_on_boundary": True}})
        result = build_microcycle_partition(index, inventory)
        self.assertEqual(result["summary"]["merged_count"], 0)
        self.assertEqual(result["summary"]["classification_counts"]["connector-only"], 2)
        self.assertIn("no-policy-or-shape-improvement", result["merge_rejection_summary"])
        self.assertTrue(all(region["repair_outcome"]["reason"] == "no-valid-improving-neighbor" for region in result["regions"]))

    def test_successive_repairs_retain_a_binary_tree_and_an_active_antichain(self):
        index, topology = grid_graph(3, 1)
        index.segments["1,0/2,0"]["tags"]["surface"] = "asphalt"
        inventory = build_atomic_region_inventory(index, topology,
                        {"edge_profile": {"require_known_surface_on_boundary": True}})
        result = build_microcycle_partition(index, inventory)
        self.assertEqual(result["summary"]["merged_count"], 2)
        self.assertEqual(len(result["regions"]), 1)
        root = result["regions"][0]
        self.assertEqual(root["repair_sequence"], ["microcycle-merge-1", "microcycle-merge-2"])
        self.assertEqual(root["level"], 2)
        self.assertEqual(root["perimeter_m"], 800)
        self.assertEqual(len(root["atomic_region_ids"]), 3)
        self.assertEqual(len(result["nodes"]), 5)
        self.assertTrue(verify_microcycle_partition(index, json.loads(json.dumps(result))))

    def test_known_shared_edge_cannot_qualify_an_unknown_merged_boundary(self):
        index, topology = two_square_graph()
        topology["face_embedding"]["faces"][0]["area_m2"] = 1
        index.segments["be"]["tags"]["surface"] = "asphalt"
        inventory = build_atomic_region_inventory(index, topology,
                        {"edge_profile": {"require_known_surface_on_boundary": True}})
        result = build_microcycle_partition(index, inventory)
        self.assertEqual(result["summary"]["merged_count"], 0)
        self.assertIn("accepted-neighbor-regression", result["merge_rejection_summary"])
        self.assertEqual(result["summary"]["classification_counts"]["accepted"], 1)

    def test_hole_creating_merge_is_rejected(self):
        first = {"cell-0-0", "cell-1-0", "cell-2-0", "cell-0-1"}
        second = {"cell-0-2", "cell-1-2", "cell-2-2", "cell-2-1"}
        index, inventory = grouped_grid([first, second], 0.95)
        result = build_microcycle_partition(index, inventory, {"maximum_atomic_regions_per_microcycle": 2})
        self.assertIn("non-simple-boundary", result["merge_rejection_summary"])
        for node in result["nodes"]:
            if node["children"]:
                self.assertIsNotNone(ordered_boundary(index, node["boundary_segment_ids"]))
        self.assertTrue(verify_microcycle_partition(index, result))

    def test_legal_children_can_have_no_legal_union_orientation(self):
        index, topology = two_square_graph()
        index.segments["ab"]["tags"]["surface"] = "asphalt"
        permitted = {"ab:f", "be:f", "de:b", "ad:b", "ef:f", "cf:b", "bc:b"}
        graph = copy.deepcopy(index.document)
        graph["arcs"] = [arc for arc in graph["arcs"] if arc["id"] in permitted]
        index = GraphIndex(graph)
        inventory = build_atomic_region_inventory(index, topology,
                        {"edge_profile": {"require_known_surface_on_boundary": True}})
        self.assertTrue(all(region["legal_orientations"] for region in inventory["regions"]))
        result = build_microcycle_partition(index, inventory)
        self.assertEqual(result["summary"]["merged_count"], 0)
        self.assertIn("no-legal-orientation", result["merge_rejection_summary"])

    def test_invalid_atomic_regions_are_retained_with_their_reasons(self):
        index, topology = two_square_graph()
        index.segments["ab"]["tags"]["access"] = "destination"
        inventory = build_atomic_region_inventory(index, topology,
                        {"edge_profile": {"forbidden_access_values": ["destination"]}})
        result = build_microcycle_partition(index, inventory)
        self.assertEqual(result["summary"]["merged_count"], 0)
        invalid = next(region for region in result["regions"] if region["status"] == "invalid")
        self.assertEqual(invalid["repair_outcome"]["reason"], "invalid-atomic-region")
        self.assertEqual(invalid["rejection_reasons"], ["forbidden-boundary"])
        self.assertEqual(len(result["base_face_to_microcycle"]), 2)

    def test_explicit_limits_leave_a_partition_with_diagnostic_reasons(self):
        index, topology = two_square_graph()
        index.segments["ab"]["tags"]["surface"] = "asphalt"
        inventory = build_atomic_region_inventory(index, topology,
                        {"edge_profile": {"require_known_surface_on_boundary": True}})
        limited = build_microcycle_partition(index, inventory, {"maximum_atomic_regions_per_microcycle": 1})
        self.assertEqual(limited["summary"]["merged_count"], 0)
        self.assertIn("atomic-region-limit", limited["merge_rejection_summary"])
        budgeted = build_microcycle_partition(index, inventory, {"maximum_merges": 0})
        connector = next(region for region in budgeted["regions"] if region["status"] == "connector-only")
        self.assertEqual(connector["repair_outcome"]["reason"], "merge-budget-exhausted")

    def test_deterministic_hierarchy_and_partition_survive_serialization(self):
        index, topology = two_square_graph()
        index.segments["ab"]["tags"]["surface"] = "asphalt"
        inventory = build_atomic_region_inventory(index, topology,
                        {"edge_profile": {"require_known_surface_on_boundary": True}})
        first = build_microcycle_partition(index, inventory)
        second = build_microcycle_partition(index, json.loads(json.dumps(inventory)))
        for result in (first, second):
            result.pop("timings_seconds")
        self.assertEqual(first, second)
        first["timings_seconds"] = {}
        with tempfile.TemporaryDirectory() as directory:
            outputs = write_microcycle_outputs(index, first, directory)
            restored = json.loads(outputs["partition"].read_text())
            self.assertTrue(verify_microcycle_partition(index, restored))
            features = json.loads(outputs["geojson"].read_text())["features"]
            self.assertEqual(len(features), 1)
            self.assertEqual(features[0]["properties"]["repair_sequence"], ["microcycle-merge-1"])
            self.assertEqual(len(features[0]["properties"]["atomic_region_ids"]), 2)

    def test_overlapping_atomic_input_and_corrupt_parent_are_rejected(self):
        index, topology = two_square_graph()
        inventory = build_atomic_region_inventory(index, topology)
        corrupt = copy.deepcopy(inventory)
        corrupt["regions"][1]["member_face_ids"] = corrupt["regions"][0]["member_face_ids"]
        with self.assertRaisesRegex(ValueError, "overlapping"):
            build_microcycle_partition(index, corrupt)
        index.segments["ab"]["tags"]["surface"] = "asphalt"
        inventory = build_atomic_region_inventory(index, topology,
                        {"edge_profile": {"require_known_surface_on_boundary": True}})
        result = build_microcycle_partition(index, inventory)
        parent = next(node for node in result["nodes"] if node["children"])
        parent["perimeter_m"] += 1
        with self.assertRaisesRegex(ValueError, "perimeter identity"):
            verify_microcycle_partition(index, result)

    def test_settings_are_bounded_and_typed(self):
        index, topology = two_square_graph()
        inventory = build_atomic_region_inventory(index, topology)
        for settings in ({"maximum_merges": -1}, {"maximum_merges": True},
                         {"maximum_atomic_regions_per_microcycle": 0},
                         {"minimum_convexity_improvement": float("nan")}):
            with self.assertRaises(ValueError):
                build_microcycle_partition(index, inventory, settings)


if __name__ == "__main__":
    unittest.main()

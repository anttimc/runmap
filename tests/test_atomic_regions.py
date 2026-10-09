import copy
import json
import tempfile
import unittest

from runmap.routing.atomic_regions import (
    _articulations, atomic_regions_geojson, build_atomic_region_inventory,
    write_atomic_region_outputs,
)
from tests.test_face_search import two_square_graph
from runmap.routing.search import GraphIndex


def grid_graph(width, height):
    nodes = [{"id": f"{x},{y}", "longitude": 24 + x * 0.001, "latitude": 60 + y * 0.001}
             for x in range(width + 1) for y in range(height + 1)]
    points = {node["id"]: [node["longitude"], node["latitude"]] for node in nodes}
    segments, arcs, faces, owners = {}, [], [], {}
    for x in range(width):
        for y in range(height):
            vertices = [f"{x},{y}", f"{x+1},{y}", f"{x+1},{y+1}", f"{x},{y+1}"]
            boundary = []
            face_id = f"cell-{x}-{y}"
            for first, second in zip(vertices, vertices[1:] + vertices[:1]):
                first, second = sorted((first, second))
                segment_id = f"{first}/{second}"
                boundary.append(segment_id)
                owners.setdefault(segment_id, []).append(face_id)
                if segment_id in segments:
                    continue
                segments[segment_id] = {"id": segment_id, "from": first, "to": second,
                                        "geometry": [points[first], points[second]], "length_m": 100,
                                        "tags": {"highway": "footway"}}
                for suffix, origin, destination in (("f", first, second), ("b", second, first)):
                    arcs.append({"id": f"{segment_id}:{suffix}", "segment_id": segment_id,
                                 "from": origin, "to": destination, "length_m": 100})
            faces.append({"id": face_id, "segment_ids": boundary, "node_ids": vertices,
                          "area_m2": 10000, "geometry": [points[node] for node in vertices + vertices[:1]]})
    graph = {"nodes": nodes, "segments": list(segments.values()), "arcs": arcs, "start_snap": {"id": "0,0"}}
    embedding = {"faces": faces, "face_adjacency": [{"segment_id": segment, "faces": members}
                                                    for segment, members in owners.items() if len(members) == 2]}
    return GraphIndex(graph), {"face_embedding": embedding}


class AtomicRegionTests(unittest.TestCase):
    def test_partition_and_symmetric_interfaces_survive_serialization(self):
        index, topology = two_square_graph()
        result = build_atomic_region_inventory(index, topology)
        self.assertEqual(result["summary"]["classification_counts"]["accepted"], 2)
        self.assertEqual(set(result["base_face_to_atomic_region"]), {"left", "right"})
        first, second = result["regions"]
        self.assertEqual(first["neighbors"][0]["segment_ids"], ["be"])
        self.assertEqual(first["neighbors"][0]["interface_id"], second["neighbors"][0]["interface_id"])
        self.assertEqual(result["shared_interfaces"][0]["length_m"], 100)
        self.assertEqual(len(first["legal_orientations"]), 2)
        self.assertTrue(first["reachability"]["round_trip_reachable"])
        with tempfile.TemporaryDirectory() as directory:
            outputs = write_atomic_region_outputs(index, result, directory)
            self.assertEqual(json.loads(outputs["inventory"].read_text()), result)
            self.assertEqual(len(json.loads(outputs["geojson"].read_text())["features"]), 2)
            self.assertTrue(outputs["summary"].exists())
        again = build_atomic_region_inventory(index, topology)
        result.pop("timings_seconds")
        again.pop("timings_seconds")
        self.assertEqual(result, again)

    def test_forbidden_separator_is_internal_to_one_atomic_region(self):
        index, topology = two_square_graph()
        index.segments["be"]["tags"]["surface"] = "dirt"
        result = build_atomic_region_inventory(index, topology, {
            "edge_profile": {"forbidden_surfaces": ["dirt"]},
        })
        self.assertEqual(len(result["regions"]), 1)
        region = result["regions"][0]
        self.assertEqual(region["member_face_ids"], ["left", "right"])
        self.assertNotIn("be", region["boundary_segment_ids"])
        self.assertEqual(region["perimeter_m"], 600)
        self.assertEqual(result["shared_interfaces"], [])

    def test_forbidden_outer_boundary_is_retained_as_invalid(self):
        index, topology = two_square_graph()
        index.segments["ab"]["tags"]["highway"] = "steps"
        result = build_atomic_region_inventory(index, topology)
        invalid = next(region for region in result["regions"] if "left" in region["member_face_ids"])
        self.assertEqual(invalid["status"], "invalid")
        self.assertEqual(invalid["profile_rejections"], [{"segment_id": "ab", "reason": "highway:steps"}])
        self.assertEqual(invalid["legal_orientations"], [])
        self.assertEqual(len(atomic_regions_geojson(index, result)["features"]), 2)
        self.assertEqual(result["excluded_interfaces"][0]["reason"], "invalid-neighbor")

    def test_indoor_separator_is_dissolved_even_when_stairs_are_allowed(self):
        index, topology = two_square_graph()
        index.segments["be"]["tags"]["indoor"] = "yes"
        result = build_atomic_region_inventory(index, topology, {"stairs": {"mode": "allow"}})
        self.assertEqual(len(result["regions"]), 1)
        self.assertNotIn("be", result["regions"][0]["boundary_segment_ids"])

    def test_all_unknown_loops_are_connectors_with_legal_directions_and_interfaces(self):
        index, topology = two_square_graph()
        result = build_atomic_region_inventory(index, topology, {
            "edge_profile": {"require_known_surface_on_boundary": True},
        })
        self.assertEqual(result["summary"]["classification_counts"]["accepted"], 0)
        self.assertEqual(result["summary"]["classification_counts"]["connector-only"], 2)
        self.assertEqual(result["summary"]["all_unknown_surface_count"], 2)
        self.assertEqual(len(result["shared_interfaces"]), 1)
        for region in result["regions"]:
            self.assertEqual(region["standalone_rejection_reasons"], ["all-unknown-surface"])
            self.assertEqual(region["surface_metrics"]["unknown_surface_fraction"], 1)
            self.assertEqual(len(region["legal_orientations"]), 2)
            self.assertEqual(region["rejection_reasons"], [])

    def test_one_known_boundary_edge_accepts_a_mixed_loop(self):
        index, topology = two_square_graph()
        index.segments["ab"]["tags"]["surface"] = "ground"
        index.segments["be"]["tags"]["surface"] = "unknown"
        result = build_atomic_region_inventory(index, topology, {
            "edge_profile": {"require_known_surface_on_boundary": True},
        })
        left = next(region for region in result["regions"] if "left" in region["member_face_ids"])
        right = next(region for region in result["regions"] if "right" in region["member_face_ids"])
        self.assertEqual(left["status"], "accepted")
        self.assertEqual(left["surface_metrics"]["unknown_surface_fraction"], 0.75)
        self.assertEqual(left["standalone_rejection_reasons"], [])
        self.assertEqual(right["status"], "connector-only")
        self.assertEqual(len(left["neighbors"]), 1)

    def test_known_internal_edge_does_not_qualify_an_unknown_outer_loop(self):
        index, topology = two_square_graph()
        index.segments["be"]["tags"].update(highway="steps", surface="paved")
        result = build_atomic_region_inventory(index, topology, {
            "edge_profile": {"require_known_surface_on_boundary": True},
        })
        self.assertEqual(len(result["regions"]), 1)
        region = result["regions"][0]
        self.assertEqual(region["status"], "connector-only")
        self.assertNotIn("be", region["boundary_segment_ids"])
        self.assertEqual(region["surface_metrics"]["known_surface_segment_ids"], [])

    def test_known_forbidden_surface_still_invalidates_a_mixed_loop(self):
        index, topology = two_square_graph()
        index.segments["ab"]["tags"]["surface"] = "grass"
        result = build_atomic_region_inventory(index, topology, {
            "edge_profile": {"require_known_surface_on_boundary": True, "forbidden_surfaces": ["grass"]},
        })
        region = next(region for region in result["regions"] if "left" in region["member_face_ids"])
        self.assertEqual(region["status"], "invalid")
        self.assertEqual(region["rejection_reasons"], ["forbidden-boundary"])
        self.assertEqual(region["standalone_rejection_reasons"], [])

    def test_poor_shape_is_queued_without_losing_membership(self):
        index, topology = two_square_graph()
        topology["face_embedding"]["faces"][0]["area_m2"] = 1
        result = build_atomic_region_inventory(index, topology)
        region = next(region for region in result["regions"] if "left" in region["member_face_ids"])
        self.assertEqual(region["status"], "repair-needed")
        self.assertIn("low-convexity", region["quality_reasons"])
        self.assertEqual(len(region["legal_orientations"]), 2)

    def test_low_compactness_is_diagnostic_and_does_not_queue_repair(self):
        index, topology = two_square_graph()
        for segment in index.segments.values():
            segment["length_m"] = 1000
        result = build_atomic_region_inventory(index, topology)
        for region in result["regions"]:
            self.assertLess(region["shape_metrics"]["isoperimetric_quotient"], 0.08)
            self.assertEqual(region["status"], "accepted")
            self.assertEqual(region["quality_reasons"], [])

    def test_relaxed_convexity_accepts_small_regions_for_later_merges(self):
        index, topology = two_square_graph()
        topology["face_embedding"]["faces"][0]["area_m2"] = 2500
        relaxed = build_atomic_region_inventory(index, topology)
        region = next(region for region in relaxed["regions"] if "left" in region["member_face_ids"])
        self.assertGreater(region["shape_metrics"]["convexity_ratio"], 0.25)
        self.assertLess(region["shape_metrics"]["convexity_ratio"], 0.55)
        self.assertEqual(region["status"], "accepted")
        strict = build_atomic_region_inventory(index, topology, settings={"minimum_convexity_ratio": 0.55})
        region = next(region for region in strict["regions"] if "left" in region["member_face_ids"])
        self.assertEqual(region["status"], "repair-needed")

    def test_articulation_detection_handles_components_and_cycles(self):
        self.assertEqual(_articulations({"a": {"b"}, "b": {"a", "c"}, "c": {"b"}, "d": set()}), {"b"})
        self.assertEqual(_articulations({"a": {"b", "c"}, "b": {"a", "c"}, "c": {"a", "b"}}), set())

    def test_poor_middle_region_is_connector_only(self):
        index, topology = grid_graph(3, 1)
        topology["face_embedding"]["faces"][1]["area_m2"] = 1
        result = build_atomic_region_inventory(index, topology)
        connector = next(region for region in result["regions"] if "cell-1-0" in region["member_face_ids"])
        self.assertEqual(connector["status"], "connector-only")
        self.assertEqual(len(connector["neighbors"]), 2)
        self.assertIn("adjacency-articulation", connector["quality_reasons"])

    def test_must_link_ring_with_hole_is_retained_as_invalid(self):
        index, topology = grid_graph(3, 3)
        for interface in topology["face_embedding"]["face_adjacency"]:
            if "cell-1-1" not in interface["faces"]:
                index.segments[interface["segment_id"]]["tags"]["surface"] = "dirt"
        result = build_atomic_region_inventory(index, topology, {"edge_profile": {"forbidden_surfaces": ["dirt"]}},
                                               {"fill_enclosed_holes": False})
        ring = next(region for region in result["regions"] if len(region["member_face_ids"]) == 8)
        self.assertEqual(ring["status"], "invalid")
        self.assertIn("non-simple-boundary", ring["rejection_reasons"])
        self.assertEqual(result["summary"]["base_face_count"], 9)
        self.assertEqual(len(atomic_regions_geojson(index, result)["features"]), 2)

    def test_fills_ring_by_absorbing_inner_faces_and_canceling_their_boundary(self):
        index, topology = grid_graph(3, 3)
        for interface in topology["face_embedding"]["face_adjacency"]:
            if "cell-1-1" not in interface["faces"]:
                index.segments[interface["segment_id"]]["tags"]["surface"] = "dirt"
        index.document["start_snap"] = {"id": "1,1"}
        result = build_atomic_region_inventory(index, topology, {"edge_profile": {"forbidden_surfaces": ["dirt"]}})
        self.assertEqual(len(result["regions"]), 1)
        region = result["regions"][0]
        self.assertEqual(region["status"], "accepted")
        self.assertEqual(region["area_m2"], 90000)
        self.assertEqual(region["perimeter_m"], 1200)
        self.assertEqual(len(region["member_face_ids"]), 9)
        self.assertEqual(len(region["source_profile_region_ids"]), 2)
        self.assertEqual(result["summary"]["filled_hole_count"], 1)
        fill = region["hole_fills"][0]
        self.assertEqual(fill["absorbed_face_ids"], ["cell-1-1"])
        self.assertEqual(len(fill["removed_boundary_segment_ids"]), 4)
        self.assertTrue(fill["holes"][0]["round_trip_reachable"])
        self.assertEqual(set(result["base_face_to_atomic_region"].values()), {region["id"]})
        self.assertEqual(len(set(result["base_face_to_profile_region"].values())), 2)
        self.assertEqual(atomic_regions_geojson(index, result)["features"][0]["properties"]["hole_fills"], [fill])
        again = build_atomic_region_inventory(index, topology, {"edge_profile": {"forbidden_surfaces": ["dirt"]}})
        for inventory in (result, again):
            inventory.pop("timings_seconds")
        self.assertEqual(json.loads(json.dumps(result)), again)

    def test_fills_an_unreachable_inner_loop(self):
        index, topology = grid_graph(3, 3)
        for interface in topology["face_embedding"]["face_adjacency"]:
            if "cell-1-1" not in interface["faces"]:
                index.segments[interface["segment_id"]]["tags"]["surface"] = "dirt"
        result = build_atomic_region_inventory(index, topology, {"edge_profile": {"forbidden_surfaces": ["dirt"]}})
        index.document["nodes"].append({"id": "home", "longitude": 24, "latitude": 60})
        index.document["start_snap"] = {"id": "home"}
        unreachable = build_atomic_region_inventory(GraphIndex(index.document), topology,
                         {"edge_profile": {"forbidden_surfaces": ["dirt"]}})
        self.assertEqual(unreachable["regions"][0]["boundary_segment_ids"], result["regions"][0]["boundary_segment_ids"])
        self.assertFalse(unreachable["hole_fills"][0]["holes"][0]["round_trip_reachable"])

    def test_hole_filling_does_not_hide_forbidden_outer_edges(self):
        index, topology = grid_graph(3, 3)
        for interface in topology["face_embedding"]["face_adjacency"]:
            if "cell-1-1" not in interface["faces"]:
                index.segments[interface["segment_id"]]["tags"]["surface"] = "dirt"
        index.segments["0,0/1,0"]["tags"]["surface"] = "dirt"
        result = build_atomic_region_inventory(index, topology, {"edge_profile": {"forbidden_surfaces": ["dirt"]}})
        self.assertEqual(result["summary"]["filled_hole_count"], 1)
        self.assertEqual(result["regions"][0]["status"], "invalid")
        self.assertEqual(result["regions"][0]["rejection_reasons"], ["forbidden-boundary"])

    def test_hole_without_retained_faces_cannot_be_silently_dropped(self):
        index, topology = grid_graph(3, 3)
        embedding = topology["face_embedding"]
        embedding["faces"] = [face for face in embedding["faces"] if face["id"] != "cell-1-1"]
        embedding["face_adjacency"] = [item for item in embedding["face_adjacency"] if "cell-1-1" not in item["faces"]]
        for interface in embedding["face_adjacency"]:
            index.segments[interface["segment_id"]]["tags"]["surface"] = "dirt"
        result = build_atomic_region_inventory(index, topology, {"edge_profile": {"forbidden_surfaces": ["dirt"]}})
        self.assertEqual(result["summary"]["filled_hole_count"], 0)
        self.assertEqual(result["regions"][0]["status"], "invalid")
        self.assertEqual(result["hole_fill_diagnostics"][0]["reason"], "incomplete-hole-coverage")

    def test_closed_way_accepts_backward_source_half_edge(self):
        index, topology = two_square_graph()
        points = topology["face_embedding"]["faces"][0]["geometry"]
        graph = {"nodes": [index.nodes["a"]], "segments": [{"id": "loop", "from": "a", "to": "a",
                  "length_m": 400, "geometry": points, "tags": {}}],
                 "arcs": [{"id": f"loop:{suffix}", "segment_id": "loop", "from": "a", "to": "a", "length_m": 400}
                          for suffix in ("f", "b")], "start_snap": {"id": "a"}}
        topology = {"face_embedding": {"faces": [{"id": "loop-face", "segment_ids": ["loop"],
                    "node_ids": ["a"], "half_edge_ids": ["loop:b"], "geometry": points, "area_m2": 10000}],
                    "face_adjacency": []}}
        result = build_atomic_region_inventory(GraphIndex(graph), topology)
        self.assertEqual(result["regions"][0]["status"], "accepted")
        self.assertEqual([item["boundary_arc_ids"] for item in result["regions"][0]["legal_orientations"]], [["loop:f"], ["loop:b"]])

    def test_inconsistent_one_way_edges_have_no_legal_orientation(self):
        index, topology = two_square_graph()
        graph = copy.deepcopy(index.document)
        graph["arcs"] = [arc for arc in graph["arcs"] if arc["id"] not in {"ab:f", "be:b"}]
        index = GraphIndex(graph)
        result = build_atomic_region_inventory(index, topology)
        region = next(region for region in result["regions"] if "left" in region["member_face_ids"])
        self.assertEqual(region["status"], "invalid")
        self.assertIn("no-legal-orientation", region["rejection_reasons"])

    def test_unreachable_valid_region_is_not_discarded(self):
        index, topology = two_square_graph()
        index.nodes["home"] = {"id": "home", "longitude": 24, "latitude": 60}
        index.document["start_snap"] = {"id": "home"}
        result = build_atomic_region_inventory(index, topology)
        self.assertEqual(result["summary"]["unreachable_count"], 2)
        self.assertTrue(all(region["status"] == "accepted" for region in result["regions"]))

    def test_geometric_crossing_is_invalid_even_when_nodes_form_a_cycle(self):
        index, topology = two_square_graph()
        index.segments["ab"]["geometry"] = [[24, 60], [24.001, 60.0008], [24, 60.0008], [24.001, 60]]
        result = build_atomic_region_inventory(index, topology)
        region = next(region for region in result["regions"] if "left" in region["member_face_ids"])
        self.assertIn("self-intersecting-boundary", region["rejection_reasons"])

    def test_near_reversal_is_hard_invalid(self):
        index, topology = two_square_graph()
        # The last tangent of ab doubles back against the upward be edge.
        index.segments["ab"]["geometry"] = [[24, 60], [24.001, 60.0001], [24.001, 60]]
        result = build_atomic_region_inventory(index, topology)
        region = next(region for region in result["regions"] if "left" in region["member_face_ids"])
        self.assertIn("near-reversal", region["rejection_reasons"])
        self.assertEqual(region["legal_orientations"], [])

    def test_corrupt_dual_and_half_edge_incidences_are_rejected(self):
        index, topology = two_square_graph()
        corrupted = copy.deepcopy(topology)
        corrupted["face_embedding"]["face_adjacency"] = []
        with self.assertRaisesRegex(ValueError, "dual incidences"):
            build_atomic_region_inventory(index, corrupted)
        topology["face_embedding"]["faces"][0]["half_edge_ids"] = ["bad"]
        with self.assertRaisesRegex(ValueError, "half-edge incidence"):
            build_atomic_region_inventory(index, topology)

    def test_invalid_shape_settings_are_rejected(self):
        index, topology = two_square_graph()
        for value in (-1, 2, float("nan")):
            with self.assertRaises(ValueError):
                build_atomic_region_inventory(index, topology, settings={"minimum_convexity_ratio": value})


if __name__ == "__main__":
    unittest.main()

import copy
import unittest

from runmap.routing.atomic_regions import build_atomic_region_inventory
from runmap.routing.profiles import classify_segments, forbidden_reason
from runmap.routing.search import GraphIndex
from tests.test_face_search import two_square_graph


STRICT_ACCESS = {"forbidden_access_values": [
    "no", "private", "destination", "customers", "permit", "delivery",
]}


class PedestrianAccessProfileTests(unittest.TestCase):
    def test_restricted_generic_and_pedestrian_access_are_excluded(self):
        for key in ("access", "foot"):
            for value in STRICT_ACCESS["forbidden_access_values"]:
                with self.subTest(key=key, value=value):
                    self.assertEqual(forbidden_reason({"tags": {key: value}}, STRICT_ACCESS), f"{key}:{value}")

    def test_explicit_pedestrian_permission_overrides_generic_restriction(self):
        for foot in ("yes", "designated", "permissive", "official"):
            with self.subTest(foot=foot):
                self.assertIsNone(forbidden_reason({"tags": {"access": "private", "foot": foot}}, STRICT_ACCESS))
        self.assertEqual(forbidden_reason({"tags": {"access": "yes", "foot": "destination"}}, STRICT_ACCESS),
                         "foot:destination")

    def test_missing_access_is_allowed_and_tags_are_normalized(self):
        self.assertIsNone(forbidden_reason({"tags": {}}, STRICT_ACCESS))
        self.assertEqual(forbidden_reason({"tags": {"access": " Destination "}}, STRICT_ACCESS), "access:destination")

    def test_restricted_outer_edge_is_invalid_and_shared_interface_is_removed(self):
        index, topology = two_square_graph()
        index.segments["ab"]["tags"]["access"] = "destination"
        result = build_atomic_region_inventory(index, topology, {"edge_profile": STRICT_ACCESS})
        left = next(region for region in result["regions"] if "left" in region["member_face_ids"])
        self.assertEqual(left["status"], "invalid")
        self.assertEqual(left["profile_rejections"], [{"segment_id": "ab", "reason": "access:destination"}])
        self.assertEqual(result["shared_interfaces"], [])

    def test_restricted_separator_is_dissolved_without_traversing_it(self):
        index, topology = two_square_graph()
        index.segments["be"]["tags"]["foot"] = "customers"
        result = build_atomic_region_inventory(index, topology, {"edge_profile": STRICT_ACCESS})
        self.assertEqual(len(result["regions"]), 1)
        region = result["regions"][0]
        self.assertEqual(region["status"], "accepted")
        self.assertEqual(region["perimeter_m"], 600)
        self.assertNotIn("be", region["boundary_segment_ids"])
        self.assertTrue(all("be:f" not in option["boundary_arc_ids"] and "be:b" not in option["boundary_arc_ids"]
                            for option in region["legal_orientations"]))

    def test_restricted_access_stem_cannot_supply_outward_or_return_access(self):
        index, topology = two_square_graph()
        graph = copy.deepcopy(index.document)
        graph["nodes"].append({"id": "home", "longitude": 23.999, "latitude": 60})
        graph["segments"].append({"id": "stem", "from": "home", "to": "a", "length_m": 100,
                                   "geometry": [[23.999, 60], [24, 60]], "tags": {"access": "permit"}})
        for suffix, first, second in (("f", "home", "a"), ("b", "a", "home")):
            graph["arcs"].append({"id": f"stem:{suffix}", "segment_id": "stem", "from": first, "to": second, "length_m": 100})
        graph["start_snap"] = {"id": "home"}
        blocked = build_atomic_region_inventory(GraphIndex(graph), topology, {"edge_profile": STRICT_ACCESS})
        self.assertEqual(blocked["summary"]["unreachable_count"], 2)
        graph["segments"][-1]["tags"]["foot"] = "yes"
        allowed = build_atomic_region_inventory(GraphIndex(graph), topology, {"edge_profile": STRICT_ACCESS})
        self.assertEqual(allowed["summary"]["unreachable_count"], 0)

    def test_restricted_gate_blocks_incident_segments_with_pedestrian_override(self):
        index, _ = two_square_graph()
        index.nodes["b"].update(barrier="gate", access="destination")
        reasons = classify_segments(index, STRICT_ACCESS)
        self.assertEqual(set(reasons), {"ab", "bc", "be"})
        self.assertEqual(reasons["be"], "node:b:access:destination")
        index.nodes["b"]["foot"] = "yes"
        self.assertEqual(classify_segments(index, STRICT_ACCESS), {})


if __name__ == "__main__":
    unittest.main()

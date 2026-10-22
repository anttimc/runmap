from dataclasses import replace
import unittest

from test_routing import graph_document
from runmap.routing.profiles import classify_segments, forbidden_reason
from runmap.routing.search import build_graph_index


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

    def test_restricted_gate_blocks_incident_segments_with_pedestrian_override(self):
        index = build_graph_index(graph_document(
            [(node_id, 24.8 + number * .001, 60.2)
             for number, node_id in enumerate(("a", "b", "c", "e"))],
            [(segment_id, first, second, 100, 0, {})
                for segment_id, first, second in (
                    ("ab", "a", "b"), ("bc", "b", "c"), ("be", "b", "e")
                )], start="a"))
        index = replace(index, nodes=dict(index.nodes))
        index.nodes["b"].update(barrier="gate", access="destination")
        reasons = classify_segments(index, STRICT_ACCESS)
        self.assertEqual(set(reasons), {"ab", "bc", "be"})
        self.assertEqual(reasons["be"], "node:b:access:destination")
        index.nodes["b"]["foot"] = "yes"
        self.assertEqual(classify_segments(index, STRICT_ACCESS), {})


if __name__ == "__main__":
    unittest.main()

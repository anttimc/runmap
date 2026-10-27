"""Visibility classification uses physical topology, never shortest-path trees."""

from copy import deepcopy
import unittest

from runmap.paths.dead_ends import classify_building_access
from runmap.routing.graph import build_graph_index
from runmap.routing.types import GraphDocument


def graph_fixture(edges: list[tuple[str, str, str, str]], entrance: str = "") -> GraphDocument:
    segments = []
    arcs = []
    nodes = sorted({node for _, first, second, _ in edges for node in (first, second)})
    for segment_id, first, second, kind in edges:
        tags = {"highway": "service", "service": "driveway"} if kind == "driveway" else {"highway": kind}
        segments.append({"id": segment_id, "osm_way_id": segment_id, "from": first, "to": second,
                         "geometry": [[24.8, 60.2], [24.8005, 60.2]], "length_m": 30.0, "tags": tags})
        arcs.extend({"id": segment_id + suffix, "segment_id": segment_id,
                     "from": a, "to": b, "length_m": 30.0}
                    for suffix, a, b in ((":f", first, second), (":b", second, first)))
    return {"nodes": [{"id": node, "longitude": 24.8, "latitude": 60.2,
                       "entrance": " yes " if node == entrance else ""} for node in nodes],
            "segments": segments, "arcs": arcs}


CORE = [("ab", "a", "b", "residential"), ("bc", "b", "c", "residential"),
        ("ca", "c", "a", "residential")]


class DeadEndTests(unittest.TestCase):
    def classify(self, edges: list[tuple[str, str, str, str]], entrance: str = "") -> set[str]:
        graph = graph_fixture(CORE + edges, entrance)
        return set(classify_building_access(build_graph_index(graph), lambda _: False, True))

    def test_driveway_chain_and_metadata(self) -> None:
        graph = graph_fixture(CORE + [("ax", "a", "x", "driveway"), ("xy", "x", "y", "driveway")])
        result = classify_building_access(build_graph_index(graph), lambda _: False, True)
        self.assertEqual(set(result), {"ax", "xy"})
        self.assertEqual(result["xy"].attachment_node, "a")
        self.assertEqual(result["xy"].branch_length_m, 60)
        self.assertEqual(result["xy"].branch_id, "ax")

    def test_entrance_stairs_and_generic_service(self) -> None:
        for kind in ("footway", "steps", "service"):
            with self.subTest(kind=kind):
                self.assertEqual(self.classify([("ax", "a", "x", kind)], "x"), {"ax"})
                self.assertEqual(self.classify([("ax", "a", "x", kind)]), set())

    def test_negative_entrance_and_length_limit(self) -> None:
        graph = graph_fixture(CORE + [("ax", "a", "x", "footway")], "x")
        for value in ("no", " EXIT ", "false", "0", "disused"):
            graph["nodes"][-1]["entrance"] = value
            self.assertFalse(classify_building_access(build_graph_index(graph), lambda _: False, True))
        graph["nodes"][-1]["entrance"] = "main"
        graph["segments"][-1]["length_m"] = 151
        self.assertFalse(classify_building_access(build_graph_index(graph), lambda _: False, True))
        graph = graph_fixture(CORE + [("ax", "a", "x", "footway"), ("xy", "x", "y", "footway")], "y")
        for segment in graph["segments"][-2:]:
            segment["length_m"] = 80
        self.assertFalse(classify_building_access(build_graph_index(graph), lambda _: False, True))

    def test_shared_parent_only_when_all_children_qualify(self) -> None:
        edges = [("ax", "a", "x", "driveway"), ("xy", "x", "y", "driveway"),
                 ("xz", "x", "z", "driveway")]
        self.assertEqual(self.classify(edges), {"ax", "xy", "xz"})
        edges[-1] = ("xz", "x", "z", "path")
        self.assertEqual(self.classify(edges), {"xy"})
        edges[-1] = ("xz", "x", "z", "residential")
        self.assertEqual(self.classify(edges), {"xy"})

    def test_branched_entrances_and_display_profile_invariance(self) -> None:
        from runmap.routing.profiles import exploration_profile
        from runmap.routing.graph import apply_profile
        graph = graph_fixture(CORE + [("ax", "a", "x", "footway"),
                                      ("xy", "x", "y", "steps"), ("xz", "x", "z", "footway")], "y")
        graph["nodes"][-1]["entrance"] = "main"
        routing = build_graph_index(graph)
        for surface in ("all", "surfaced"):
            for avoid_stairs in (False, True):
                index = apply_profile(routing, exploration_profile(surface, avoid_stairs))
                self.assertEqual(set(classify_building_access(index, lambda _: False, True)), {"ax", "xy", "xz"})
        graph = deepcopy(graph)
        graph["nodes"][-1]["foot"] = "no"
        self.assertNotIn("xz", classify_building_access(build_graph_index(graph), lambda _: False, True))

    def test_through_entrance_scenic_and_incompatible_parent(self) -> None:
        self.assertEqual(self.classify([("ax", "a", "x", "footway"),
                                        ("xb", "x", "b", "footway")], "x"), set())
        self.assertEqual(self.classify([("ax", "a", "x", "path")], "x"), set())
        self.assertEqual(self.classify([("ax", "a", "x", "residential"),
                                        ("xy", "x", "y", "footway")], "y"), {"xy"})

    def test_boundary_uncertainty_and_tree_only_component(self) -> None:
        graph = graph_fixture(CORE + [("ax", "a", "x", "driveway")])
        index = build_graph_index(graph)
        self.assertFalse(classify_building_access(index, lambda _: True, True))
        self.assertFalse(classify_building_access(index, lambda _: False, False))
        tree = graph_fixture([("xy", "x", "y", "driveway"), ("yz", "y", "z", "driveway")])
        self.assertFalse(classify_building_access(build_graph_index(tree), lambda _: False, True))

    def test_parallel_self_loop_turning_loop_and_connector(self) -> None:
        for edges in (
            [("ax", "a", "x", "driveway"), ("xa", "x", "a", "driveway")],
            [("ax", "a", "x", "driveway"), ("xx", "x", "x", "driveway")],
            [("ax", "a", "x", "driveway"), ("xy", "x", "y", "driveway"),
             ("yz", "y", "z", "driveway"), ("zx", "z", "x", "driveway")],
        ):
            with self.subTest(edges=edges):
                self.assertEqual(self.classify(edges, "x"), set())

    def test_public_permissions_and_prepared_boundaries(self) -> None:
        graph = graph_fixture(CORE + [("ax", "a", "x", "driveway")])
        for tags in ({"access": "private"}, {"foot": "no"},
                     {"foot:forward": "no", "foot:backward": "no"}):
            graph = deepcopy(graph)
            graph["segments"][-1]["tags"] = {"highway": "service", "service": "driveway", **tags}
            self.assertFalse(classify_building_access(build_graph_index(graph), lambda _: False, True))
        graph = deepcopy(graph)
        graph["segments"][-1]["tags"] = {"highway": "service", "service": "driveway", "oneway:foot": "yes"}
        index = build_graph_index(graph)
        self.assertEqual(set(classify_building_access(index, lambda _: False, True)), {"ax"})
        index.boundary_blocked_segments.add("ax")
        self.assertFalse(classify_building_access(index, lambda _: False, True))

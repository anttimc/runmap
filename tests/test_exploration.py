"""Analytically checked partial-edge outings and shared access policy."""
import copy
from tempfile import TemporaryDirectory
import unittest

from test_routing import graph_document
from test_path_api import fixture
from runmap.api.paths import load_path_data, snap_path_start, exploration_context, inspect_path, inspect_outing, path_policy_index
from runmap.paths.graph import write_graph_outputs
from runmap.paths.exploration import Snap, ExplorationCache, interval_inclusion, distance_from_start
from runmap.routing_reference.exploration import distance_data, split_start_graph, cached_distances
from runmap.routing.graph import build_graph_index
from runmap.routing_reference.search import shortest_path, shortest_distances, external_node_distances


def graph(edges):
    names = sorted({node for edge in edges for node in edge[1:3]})
    return graph_document([(name, 24 + i * .001, 60) for i, name in enumerate(names)],
                          [(sid, a, b, length, 0, {"highway": "path"}) for sid, a, b, length in edges])


def snap(segment, offset):
    return Snap(segment, offset, offset, 24, 60, 0)


class InclusionTests(unittest.TestCase):
    def test_outside_budget_and_disconnected_intervals_share_one_state(self) -> None:
        index = build_graph_index(graph([
            ("stem", "s", "a", 100), ("far", "a", "b", 1000), ("other", "x", "y", 10),
        ]))
        distances = distance_data(index, snap("stem", 0), 50)
        self.assertEqual(external_node_distances(distances.outward), {"s": 0})
        far = interval_inclusion(index, distances, "far", 0, 10, 50)
        disconnected = interval_inclusion(index, distances, "other", 0, 10, 50)
        self.assertEqual(far, disconnected)
        self.assertEqual(far["inclusion_state"], "beyond_budget")
        self.assertIsNone(far["minimum_inclusion_m"])
        self.assertEqual(far["distance_lower_bound_m"], 50)
        self.assertTrue(all("connected" not in direction for direction in far["directions"]))

    def test_outward_distance_is_network_distance_not_round_trip(self):
        index = build_graph_index(graph([("stem", "a", "b", 100), ("branch", "b", "c", 300),
                                         ("other", "x", "y", 10)]))
        distances = distance_data(index, snap("stem", 40), 200)
        self.assertEqual(distance_from_start(index, distances, "stem", 40), 0)
        self.assertEqual(distance_from_start(index, distances, "stem", 10), 30)
        self.assertEqual(distance_from_start(index, distances, "branch", 100), 160)
        self.assertIsNone(distance_from_start(index, distances, "branch", 250))
        self.assertIsNone(distance_from_start(index, distances, "other", 5))

    def test_dead_end_partial_long_edge_exact_budget_and_monotonicity(self):
        index = build_graph_index(graph([("long", "a", "b", 1000)]))
        distances = distance_data(index, snap("long", 100), 500)
        self.assertEqual(interval_inclusion(index, distances, "long", 100, 250, 150)["minimum_inclusion_m"], 150)
        self.assertEqual(interval_inclusion(index, distances, "long", 100, 250, 150)["inclusion_state"], "included")
        self.assertEqual(interval_inclusion(index, distances, "long", 100, 250, 149)["inclusion_state"], "beyond_budget")
        self.assertEqual(interval_inclusion(index, distances, "long", 40, 160, 240)["minimum_inclusion_m"], 180)
        previous = set()
        for budget in (50, 100, 200, 500, 2000):
            distances = distance_data(index, snap("long", 100), budget)
            included = {a for a in range(0, 1000, 10) if interval_inclusion(index, distances, "long", a, a+10, budget)["inclusion_state"] == "included"}
            self.assertTrue(previous <= included)
            previous = included

    def test_stem_to_loop_and_disconnected_component(self):
        index = build_graph_index(graph([("stem", "s", "a", 100), ("ab", "a", "b", 100),
                                         ("bc", "b", "c", 100), ("ca", "c", "a", 100),
                                         ("other", "x", "y", 10)]))
        distances = distance_data(index, snap("stem", 0), 1000)
        self.assertEqual(interval_inclusion(index, distances, "bc", 0, 100, 500)["minimum_inclusion_m"], 300)
        self.assertEqual(interval_inclusion(index, distances, "other", 0, 10, 1000)["inclusion_state"], "beyond_budget")

    def test_one_way_branch_and_one_way_interior_with_return_cycle(self):
        document = graph([("stem", "s", "a", 100), ("branch", "a", "b", 100)])
        document["arcs"] = [arc for arc in document["arcs"] if arc["id"] != "branch:b"]
        index = build_graph_index(document)
        distances = distance_data(index, snap("stem", 0), 1000)
        self.assertEqual(interval_inclusion(index, distances, "branch", 0, 10, 1000)["inclusion_state"], "included")
        distances = distance_data(index, snap("branch", 50), 1000)
        self.assertEqual(interval_inclusion(index, distances, "branch", 60, 70, 1000)["inclusion_state"], "included")
        document = graph([("ab", "a", "b", 100), ("bc", "b", "c", 100), ("ca", "c", "a", 100)])
        document["arcs"] = [arc for arc in document["arcs"] if arc["id"].endswith(":f")]
        index = build_graph_index(document)
        distances = distance_data(index, snap("ab", 50), 300)
        self.assertEqual(interval_inclusion(index, distances, "ab", 60, 70, 300)["minimum_inclusion_m"], 20)
        self.assertEqual(interval_inclusion(index, distances, "ab", 40, 60, 600)["minimum_inclusion_m"], 310)

    def test_closed_segment_and_explicit_backward_permission(self):
        document = graph([("loop", "a", "a", 100)])
        document["segments"][0]["tags"]["oneway:foot"] = "-1"
        index = build_graph_index(document)
        split, start = split_start_graph(index, snap("loop", 30))
        self.assertEqual(shortest_distances(split, start)["a"], 30)
        self.assertEqual(shortest_distances(split, start, reverse=True)["a"], 70)
        distances = distance_data(index, snap("loop", 30), 100)
        self.assertEqual(interval_inclusion(index, distances, "loop", 10, 20, 100)["minimum_inclusion_m"], 20)
        self.assertEqual(interval_inclusion(index, distances, "loop", 20, 40, 200)["minimum_inclusion_m"], 110)

    def test_restricted_nodes_have_no_destination_or_start_exception(self):
        document = graph([("ab", "a", "b", 100)])
        document["nodes"][1].update(foot=" Private ")
        index = build_graph_index(document)
        self.assertIsNone(shortest_path(index, "a", "b"))
        self.assertIsNone(shortest_path(index, "b", "a"))
        self.assertEqual(shortest_distances(index, "b"), {})
        distances = distance_data(index, snap("ab", 50), 1000)
        self.assertEqual(interval_inclusion(index, distances, "ab", 0, 100, 1000)["inclusion_state"], "excluded")
        document = copy.deepcopy(document)
        document["nodes"][1].update(foot="yes", access="private")
        self.assertIsNotNone(shortest_path(build_graph_index(document), "a", "b"))

    def test_explicit_direction_is_shared_by_forward_reverse_and_cost_search(self):
        document = graph([("ab", "a", "b", 100)])
        document["segments"][0]["tags"]["foot:forward"] = " No "
        index = build_graph_index(document)
        self.assertIsNone(shortest_path(index, "a", "b"))
        self.assertIsNotNone(shortest_path(index, "b", "a"))
        self.assertEqual(shortest_distances(index, "a"), {"a": 0})
        self.assertEqual(shortest_distances(index, "a", reverse=True), {"a": 0, "b": 100})

    def test_temporary_split_does_not_mutate_original_graph(self):
        document = graph([("ab", "a", "b", 100)])
        before = copy.deepcopy(document)
        index = build_graph_index(document)
        split, start = split_start_graph(index, snap("ab", 30))
        self.assertEqual(shortest_distances(split, start), {start: 0, "a": 30, "b": 70})
        self.assertEqual(shortest_distances(split, start, reverse=True), {start: 0, "a": 30, "b": 70})
        self.assertEqual(document, before)
        self.assertNotIn(start, index.nodes)

    def test_cache_reuses_larger_limit_but_expands_for_larger_request(self):
        index = build_graph_index(graph([("ab", "a", "b", 1000)]))
        cache = ExplorationCache(capacity=2)
        a, hit = cached_distances(cache, "data", index, snap("ab", 0), 100)
        self.assertFalse(hit)
        b, hit = cached_distances(cache, "data", index, snap("ab", 0), 50)
        self.assertTrue(hit)
        self.assertIs(a, b)
        c, hit = cached_distances(cache, "data", index, snap("ab", 0), 2000)
        self.assertFalse(hit)
        self.assertIn("b", external_node_distances(c.outward))
        self.assertEqual(interval_inclusion(index, a, "ab", 900, 910, 100)["inclusion_state"], "beyond_budget")

    def test_distances_reject_an_unrelated_integer_ordering(self) -> None:
        document = graph([("ab", "a", "b", 100)])
        index = build_graph_index(document)
        distances = distance_data(index, snap("ab", 30), 200)
        other = build_graph_index(copy.deepcopy(document))
        with self.assertRaisesRegex(ValueError, "different prepared integer graph"):
            interval_inclusion(other, distances, "ab", 0, 10, 200)
        with self.assertRaisesRegex(ValueError, "different prepared integer graph"):
            distance_from_start(other, distances, "ab", 10)


class SnapTests(unittest.TestCase):
    def load(self, document):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        outputs = write_graph_outputs(document, directory.name)
        return load_path_data(outputs["graph"])

    def test_mid_edge_offsets_eligibility_radius_and_endpoint_restrictions(self):
        data = self.load(fixture())
        result = snap_path_start(data, 24.802, 60.20001)
        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(result["snap"]["graph_offset_m"], 110, places=2)
        self.assertLess(result["snap"]["offset_m"], 2)
        self.assertEqual(snap_path_start(data, 25, 61)["status"], "no_eligible_snap")
        document = fixture()
        document["nodes"][1]["access"] = "private"
        data = self.load(document)
        self.assertEqual(snap_path_start(data, 24.802, 60.2)["status"], "no_eligible_snap")

    def test_parallel_candidates_keep_physical_connectivity_and_preference(self):
        document = fixture()
        second = copy.deepcopy(document["segments"][0])
        second.update(id="parallel", **{"from": "3", "to": "4"})
        second["tags"].update(bridge="yes", layer="1")
        second["geometry"] = [[lon, lat + .0001] for lon, lat in second["geometry"]]
        document["segments"].append(second)
        document["nodes"] += [{"id": "3", "longitude": 24.8, "latitude": 60.2001}, {"id": "4", "longitude": 24.804, "latitude": 60.2001}]
        document["arcs"].append({"id": "parallel:f", "segment_id": "parallel", "from": "3", "to": "4", "length_m": 220})
        measurements = copy.deepcopy(document["_measurements"]["segments"]["s"])
        for item in measurements:
            item["segment_id"] = "parallel"
            item["geometry"] = [[lon, lat + .0001] for lon, lat in item["geometry"]]
        document["_measurements"]["segments"]["parallel"] = measurements
        data = self.load(document)
        result = snap_path_start(data, 24.802, 60.20002)
        self.assertEqual(result["snap"]["segment_id"], "s")
        self.assertEqual({candidate["segment_id"] for candidate in result["candidates"]}, {"s", "parallel"})
        result = snap_path_start(data, 24.802, 60.20002, segment_id="parallel")
        self.assertEqual(result["snap"]["segment_id"], "parallel")
        self.assertEqual(snap_path_start(data, 24.802, 60.20002, segment_id="nonexistent")["status"], "no_eligible_snap")
        index = path_policy_index(data)
        distances = distance_data(index, snap("s", 110), 1000)
        self.assertEqual(interval_inclusion(index, distances, "parallel", 110, 120, 1000)["inclusion_state"], "beyond_budget")

    def test_search_cache_reuse_preserves_inspection_numerical_states(self) -> None:
        document = fixture()
        document["segments"][0]["backward"] = True
        document["arcs"].append({"id": "s:b", "segment_id": "s", "from": "2", "to": "1", "length_m": 220})
        data = self.load(document)
        _, first, _, hit = exploration_context(data, 24.802, 60.2, .1, "all", True, 50, None)
        self.assertFalse(hit)
        _, smaller, _, hit = exploration_context(data, 24.802, 60.2, .05, "all", True, 50, None)
        self.assertTrue(hit)
        self.assertIs(first, smaller)
        result = inspect_outing(data, inspect_path(data, "s"), 24.802, 60.2, .05)
        self.assertEqual({item["inclusion_state"] for item in result["intervals"]}, {"beyond_budget"})
        _, _, _, hit = exploration_context(data, 24.802, 60.2, .3, "all", True, 50, None)
        self.assertFalse(hit)
        result = inspect_outing(data, inspect_path(data, "s"), 24.802, 60.2, .3)
        self.assertEqual({item["inclusion_state"] for item in result["intervals"]}, {"included"})

    def test_bridge_crossing_has_candidates_without_an_artificial_junction(self):
        document = fixture()
        crossing = copy.deepcopy(document["segments"][0])
        crossing.update(id="crossing", **{"from": "3", "to": "4"})
        crossing["tags"]["bridge"] = "yes"
        crossing["geometry"] = [[24.802, 60.199 + i * .0005] for i in range(5)]
        document["segments"].append(crossing)
        document["nodes"] += [{"id": "3", "longitude": 24.802, "latitude": 60.199},
                              {"id": "4", "longitude": 24.802, "latitude": 60.201}]
        document["arcs"].append({"id": "crossing:f", "segment_id": "crossing", "from": "3", "to": "4", "length_m": 220})
        intervals = copy.deepcopy(document["_measurements"]["segments"]["s"])
        for i, item in enumerate(intervals):
            item.update(segment_id="crossing", geometry=crossing["geometry"][i:i+2], quality_flags=["bridge"])
        document["_measurements"]["segments"]["crossing"] = intervals
        data = self.load(document)
        result = snap_path_start(data, 24.802, 60.2, segment_id="crossing")
        self.assertEqual(result["snap"]["segment_id"], "crossing")
        self.assertEqual({c["segment_id"] for c in result["candidates"]}, {"s", "crossing"})
        index = path_policy_index(data)
        distances = distance_data(index, snap("s", 110), 1000)
        self.assertEqual(interval_inclusion(index, distances, "crossing", 110, 120, 1000)["inclusion_state"], "beyond_budget")
        # A missing terrain interval remains eligible for snapping.
        self.assertEqual(snap_path_start(data, 24.8025, 60.2)["snap"]["segment_id"], "s")

    def test_surface_and_stair_exclusions_leave_context_and_uncertain_connections(self):
        document = fixture()
        document["segments"][0]["tags"].update(highway="steps", bridge="yes")
        data = self.load(document)
        self.assertEqual(snap_path_start(data, 24.802, 60.2)["status"], "no_eligible_snap")
        self.assertEqual(snap_path_start(data, 24.802, 60.2, avoid_stairs=False)["status"], "ready")
        document["segments"][0]["tags"].update(highway="path")
        document["segments"][0]["tags"].pop("surface")
        data = self.load(document)
        self.assertEqual(snap_path_start(data, 24.802, 60.2)["status"], "ready")
        self.assertEqual(snap_path_start(data, 24.802, 60.2, surface="surfaced")["status"], "no_eligible_snap")


if __name__ == "__main__":
    unittest.main()

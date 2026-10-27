"""Prepared policies preserve directed routing without parsing tags in searches."""

import math
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_routing import graph_document
from runmap.paths.exploration import Snap, distance_data, legal_directions, split_start_graph
from runmap.routing.geometry import projected_points
from runmap.routing.profiles import arc_direction_allowed, exploration_profile
from runmap.routing.run_planner import RunSettings, endpoint_overlay, plan_run
from runmap.routing.search import (
    apply_profile, arc_usable, build_graph_index, shortest_distances, shortest_path,
)
from runmap.routing.types import GraphDocument


def direction_graph(tags: dict[str, str]) -> GraphDocument:
    return graph_document(
        [("a", 24.8, 60.2), ("b", 24.801, 60.2)],
        [("ab", "a", "b", 100, 0, {"highway": "path", **tags})], start="a")


def interior_snap(graph: GraphDocument, fraction: float) -> Snap:
    segment = graph["segments"][0]
    first, last = segment["geometry"]
    return Snap("ab", 100 * fraction, math.dist(*projected_points(segment["geometry"])) * fraction,
                first[0] + fraction * (last[0] - first[0]), first[1], 0)


class PreparedTraversalTests(unittest.TestCase):
    def test_prepared_directions_match_tag_checks(self) -> None:
        cases: tuple[dict[str, str], ...] = (
            {}, {"oneway": "yes"}, {"oneway:bicycle": "yes"}, {"oneway:foot": " YES "},
            {"oneway:foot": "-1"}, {"foot:forward": " Private "},
            {"foot:backward": "destination"}, {"foot:forward": "no", "foot:backward": "no"},
        )
        for tags in cases:
            with self.subTest(tags=tags):
                graph = direction_graph(tags)
                segment = graph["segments"][0]
                index = build_graph_index(graph)
                expected = {arc["id"] for arc in graph["arcs"]
                            if arc_direction_allowed(segment, arc, index.profile)}
                self.assertEqual({arc for arcs in index.outgoing.values() for arc in arcs}, expected)
                self.assertEqual({arc for arcs in index.incoming.values() for arc in arcs}, expected)
                for arc in graph["arcs"]:
                    permitted = arc["id"] in expected
                    self.assertEqual(arc_usable(index, arc), permitted)
                    self.assertEqual(shortest_path(index, arc["from"], arc["to"]) is not None, permitted)
                    self.assertEqual(arc["from"] in shortest_distances(index, arc["to"], reverse=True), permitted)

    def test_relaxing_policy_restores_arcs_and_reuses_geometry(self) -> None:
        graph = direction_graph({"highway": "steps", "foot:backward": "destination"})
        strict = build_graph_index(graph, exploration_profile(avoid_stairs=True))
        self.assertEqual(strict.outgoing, {})
        public = apply_profile(strict, exploration_profile(avoid_stairs=False))
        self.assertEqual(legal_directions(public, "ab"), {True})
        relaxed = apply_profile(public, {**public.profile, "forbidden_access_values": ["no", "private"]})
        self.assertEqual(legal_directions(relaxed, "ab"), {True, False})
        self.assertEqual(relaxed.outgoing, {"a": ["ab:f"], "b": ["ab:b"]})
        self.assertEqual(relaxed.incoming, {"b": ["ab:f"], "a": ["ab:b"]})
        self.assertEqual(legal_directions(public, "ab"), {True})
        self.assertEqual(legal_directions(strict, "ab"), set())
        self.assertIs(relaxed.projected, strict.projected)

    def test_segment_flags_missing_arcs_and_self_loops(self) -> None:
        graph = direction_graph({})
        graph["segments"][0]["backward"] = False
        self.assertEqual(legal_directions(build_graph_index(graph), "ab"), {True})
        graph = deepcopy(graph)
        graph["segments"][0]["backward"] = True
        graph["arcs"] = graph["arcs"][1:]
        self.assertEqual(legal_directions(build_graph_index(graph), "ab"), {False})
        loop = graph_document([("a", 24.8, 60.2)],
                              [("loop", "a", "a", 100, 0, {"oneway:foot": "-1"})], start="a")
        index = build_graph_index(loop)
        self.assertEqual(index.outgoing, {"a": ["loop:b"]})
        self.assertEqual(legal_directions(index, "loop"), {False})

    def test_searches_and_endpoint_splits_do_not_parse_direction_tags(self) -> None:
        for oneway in ("yes", "-1"):
            with self.subTest(oneway=oneway):
                graph = direction_graph({"oneway:foot": oneway})
                index = build_graph_index(graph)
                home, turnaround = interior_snap(graph, .2), interior_snap(graph, .8)
                with patch("runmap.routing.search.pedestrian_directions", side_effect=AssertionError), \
                        patch("runmap.routing.profiles.pedestrian_directions", side_effect=AssertionError):
                    overlay = endpoint_overlay(index, home, turnaround)
                    start, end = overlay.start, overlay.destination
                    if oneway == "-1":
                        start, end = end, start
                    self.assertIsNotNone(shortest_path(overlay.graph, start, end))
                    self.assertIsNone(shortest_path(overlay.graph, end, start))
                    self.assertEqual(shortest_distances(overlay.graph, start)[end], 60)
                    self.assertEqual(shortest_distances(overlay.graph, end, reverse=True)[start], 60)
                    exploration, virtual = split_start_graph(index, home)
                    target = "b" if oneway == "yes" else "a"
                    other = "a" if oneway == "yes" else "b"
                    self.assertIsNotNone(shortest_path(exploration, virtual, target))
                    self.assertIsNone(shortest_path(exploration, virtual, other))
                    reached = distance_data(index, home, 300).outward
                    assert index.integer is not None
                    self.assertLess(reached.values[index.integer.node_indices[target]], float("inf"))
                    result = plan_run(index, home, turnaround, RunSettings())
                    self.assertEqual(result.outcomes, ("unreachable", "unreachable"))
                self.assertEqual(set(index.direction_cache), {"ab"})

    def test_request_blocks_still_apply_to_prepared_adjacency(self) -> None:
        index = build_graph_index(direction_graph({}))
        self.assertIsNone(shortest_path(index, "a", "b", blocked_segments={"ab"}))
        self.assertEqual(shortest_distances(index, "a", blocked_segments={"ab"}), {"a": 0})
        self.assertEqual(shortest_distances(index, "b", reverse=True, blocked_segments={"ab"}), {"b": 0})
        self.assertFalse(arc_usable(index, index.arcs["ab:f"], {"ab"}))
        self.assertIsNotNone(shortest_path(index, "a", "b"))

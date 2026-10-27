"""Lossless arc publication, packed traversal, and shared policy eligibility."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest

import numpy as np

from test_integer_graph import fixture
from runmap.paths.measurements import graph_digest
from runmap.routing.arc_storage import ArcLookup, ArcRecords
from runmap.routing.integer_graph import (AdjacencyLookup, apply_integer_policy, extend_integer_graph,
                                         external_arc, filtered_adjacency, integer_arc)
from runmap.routing.profiles import exploration_profile
from runmap.routing.graph import apply_profile, build_graph_index
from runmap.routing_reference.search import external_node_distances, indexed_shortest_distances, shortest_path
from runmap.routing.serialization import prepared_json


class ArcStorageTests(unittest.TestCase):
    def test_packed_arcs_preserve_canonical_json_and_detach_records(self) -> None:
        document = fixture()
        source = document["arcs"][0]
        # Exercise both sides of the bounded checksum serialization batch.
        for number in range(1025):
            arc = {**source, "id": f"parallel-{number}:f", "length_m": number}
            if number % 2:
                arc.update(ascent_m=0, descent_m=0.5, terrain_valid_fraction=1)
            document["arcs"].append(arc)
        before = deepcopy(document)
        expected = json.dumps(before, sort_keys=True, separators=(",", ":"), allow_nan=False)
        index = build_graph_index(document)
        self.assertIsInstance(document["arcs"], ArcRecords)
        self.assertIsInstance(index.arcs, ArcLookup)
        self.assertIsInstance(index.outgoing, AdjacencyLookup)
        self.assertEqual(json.dumps(document, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False, default=prepared_json), expected)
        self.assertEqual(graph_digest(document), hashlib.sha256(expected.encode()).hexdigest())
        self.assertEqual(graph_digest(before), graph_digest(document))
        source["length_m"] = 9999
        detached = index.arcs["sz:f"]
        detached["length_m"] = 9999
        self.assertEqual(index.arcs["sz:f"], before["arcs"][0])
        self.assertEqual(document["arcs"][-1], before["arcs"][-1])
        self.assertEqual(document["arcs"][::-1], before["arcs"][::-1])
        self.assertIsInstance(deepcopy(document)["arcs"], list)
        self.assertNotIn("terrain_valid_fraction", document["arcs"][-1])
        with self.assertRaises(IndexError):
            _ = document["arcs"][len(document["arcs"])]

    def test_policies_share_topology_with_detached_read_only_masks(self) -> None:
        index = build_graph_index(fixture(), exploration_profile(avoid_stairs=False))
        strict = apply_profile(index, exploration_profile(avoid_stairs=True))
        a, b = index.integer, strict.integer
        self.assertIs(a.arcs, b.arcs)
        self.assertIs(a.outgoing, b.outgoing)
        self.assertIs(a.incoming, b.incoming)
        self.assertIs(a.arc_ids, b.arc_ids)
        self.assertIsNot(a.enabled, b.enabled)
        self.assertTrue(a.enabled[a.arc_indices["sz:f"]])
        self.assertFalse(b.enabled[b.arc_indices["sz:f"]])
        mask = np.asarray(b.enabled).copy()
        policy = apply_integer_policy(a, mask)
        mask[:] = True
        self.assertFalse(policy.enabled[b.arc_indices["sz:f"]])
        for values in (a.arcs.optional,):
            self.assertTrue(values.readonly)
            with self.assertRaises(TypeError):
                values[0, 0] = 0
        for values in (a.arcs.sources, a.arcs.targets, a.arcs.segments, a.arcs.lengths, a.arcs.reverse, a.arcs.flags,
                       a.outgoing.offsets, a.outgoing.arcs, a.incoming.arcs, a.enabled):
            with self.assertRaises(TypeError):
                values[0] = 0
        with self.assertRaises(ValueError):
            apply_integer_policy(a, np.ones(2, dtype=np.bool_))
        with self.assertRaises(ValueError):
            apply_integer_policy(a, np.ones(len(a.arc_ids), dtype=np.int8))

    def test_filtered_adjacency_preserves_masked_search_and_row_order(self) -> None:
        index = build_graph_index(fixture(), exploration_profile(avoid_stairs=True))
        base = index.integer
        filtered = replace(base, outgoing=filtered_adjacency(base.outgoing, base.enabled),
                           incoming=filtered_adjacency(base.incoming, base.enabled))
        graph = replace(index, integer=filtered)
        for reverse in (False, True):
            for start in base.node_ids:
                for maximum in (0, 100, 1000):
                    a = indexed_shortest_distances(index, base.node_indices[start], maximum, reverse=reverse)
                    b = indexed_shortest_distances(graph, base.node_indices[start], maximum, reverse=reverse)
                    self.assertEqual(list(external_node_distances(a).items()), list(external_node_distances(b).items()))

    def test_overlay_can_override_a_base_arc_without_mutating_columns(self) -> None:
        index = build_graph_index(fixture())
        base = index.integer
        original = index.arcs["sa:f"]
        updated = {**original, "length_m": 7}
        dense = extend_integer_graph(base, {}, {}, {"sa:f": updated},
                                     {"s": ["sa:f"]}, {"a": ["sa:f"]}, segment_records=index.segments)
        graph = replace(index, integer=dense)
        self.assertIs(dense.arcs, base.arcs)
        self.assertEqual(integer_arc(dense, dense.arc_indices["sa:f"]).length_m, 7)
        self.assertEqual(external_arc(dense, dense.arc_indices["sa:f"]), updated)
        self.assertEqual(external_arc(base, base.arc_indices["sa:f"]), original)
        self.assertEqual(shortest_path(graph, "s", "a").cost, 7)
        self.assertEqual(shortest_path(graph, "s", "a", arc_cost=lambda arc: arc["length_m"]).cost, 7)
        for reverse, start, end in ((False, "s", "a"), (True, "a", "s")):
            result = indexed_shortest_distances(graph, dense.node_indices[start], reverse=reverse)
            self.assertEqual(external_node_distances(result)[end], 7)

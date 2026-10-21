"""Integer search preserves external IDs, policies, and overlay isolation."""

from copy import deepcopy
import math
import random
from time import monotonic
import unittest

from test_routing import graph_document
from runmap.experiments.search_reference import shortest_path_string
from runmap.paths.exploration import Snap, split_start_graph
from runmap.routing.geometry import projected_points
from runmap.routing.integer_graph import integer_arc
from runmap.routing.profiles import exploration_profile, arc_forward
from runmap.routing.run_planner import endpoint_overlay
from runmap.routing.search import CostModel, apply_profile, build_graph_index, shortest_path
from runmap.routing.types import GraphDocument


def fixture() -> GraphDocument:
    return graph_document(
        [('s', 24.8, 60.2), ('z', 24.801, 60.201), ('a', 24.801, 60.199), ('d', 24.802, 60.2)],
        [('sz', 's', 'z', 100, 0, {'highway': 'steps'}),
         ('zd', 'z', 'd', 100, 0, {'highway': 'path'}),
         ('sa', 's', 'a', 100, 0, {'highway': 'path'}),
         ('ad', 'a', 'd', 100, 0, {'highway': 'path'})])


def snap(graph: GraphDocument, segment_id: str, fraction: float) -> Snap:
    segment = next(s for s in graph['segments'] if s['id'] == segment_id)
    first, second = segment['geometry']
    return Snap(segment_id, segment['length_m'] * fraction,
                math.dist(*projected_points(segment['geometry'])) * fraction,
                first[0] + fraction * (second[0] - first[0]),
                first[1] + fraction * (second[1] - first[1]), 0)


class IntegerGraphTests(unittest.TestCase):
    def test_ids_are_dense_without_changing_serialized_records(self) -> None:
        document = fixture()
        original = deepcopy(document)
        index = build_graph_index(document)
        dense = index.integer
        assert dense is not None
        self.assertEqual(document, original)
        for indices, count in ((dense.node_indices, len(document['nodes'])),
                               (dense.segment_indices, len(document['segments'])),
                               (dense.arc_indices, len(document['arcs']))):
            self.assertEqual(sorted(indices.values()), list(range(count)))
        for position, arc in enumerate(dense.arcs):
            self.assertEqual(arc.index, position)
            self.assertIs(arc.record, document['arcs'][position])
            self.assertEqual(arc.reverse, not arc_forward(index.segments[arc.record['segment_id']], arc.record))

    def test_indexed_callbacks_preserve_routes_and_reject_invalid_costs(self) -> None:
        index = build_graph_index(fixture())
        assert index.integer is not None
        expected = shortest_path(index, 's', 'd', arc_cost=lambda arc: arc['length_m'] * 2)
        actual = shortest_path(index, 's', 'd', indexed_arc_cost=lambda arc: arc.length_m * 2,
                               indexed_heuristic=lambda node, target: 0.0)
        self.assertEqual(actual, expected)
        for value in (-1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                shortest_path(index, 's', 'd', indexed_arc_cost=lambda arc: value)
        with self.assertRaisesRegex(ValueError, 'zero at the destination'):
            shortest_path(index, 's', 'd', indexed_heuristic=lambda node, target: 1)
        with self.assertRaisesRegex(ValueError, 'one arc-cost callback'):
            shortest_path(index, 's', 'd', arc_cost=lambda arc: 1, indexed_arc_cost=lambda arc: 1)
        with self.assertRaisesRegex(ValueError, 'one heuristic callback'):
            shortest_path(index, 's', 'd', heuristic=lambda node, target: 0,
                          indexed_heuristic=lambda node, target: 0)

    def test_heap_ties_follow_external_names_instead_of_integer_order(self) -> None:
        index = build_graph_index(fixture())
        dense = index.integer
        assert dense is not None
        self.assertLess(dense.node_indices['z'], dense.node_indices['a'])
        result = shortest_path(index, 's', 'd')
        assert result is not None
        self.assertEqual(result.arc_ids, ('sa:f', 'ad:f'))
        expected = shortest_path_string(index, 's', 'd')
        self.assertEqual(result, expected)
        assert expected is not None
        self.assertEqual(result.expanded_nodes, expected.expanded_nodes)

    def test_endpoint_overlay_shares_base_and_does_not_leak(self) -> None:
        document = fixture()
        index = build_graph_index(document)
        base = index.integer
        assert base is not None
        overlay = endpoint_overlay(index, snap(document, 'sa', .2), snap(document, 'ad', .8))
        dense = overlay.graph.integer
        assert dense is not None
        self.assertIs(dense.arcs, base.arcs)
        self.assertIs(dense.outgoing, base.outgoing)
        self.assertIs(dense.incoming, base.incoming)
        self.assertIs(dense.node_ids, base.node_ids)
        self.assertEqual(base.extra_arcs, ())
        self.assertEqual(base.outgoing_overrides, {})
        self.assertNotIn(overlay.home, base.node_indices)
        self.assertEqual(sorted(dense.node_indices.values()), list(range(len(base.node_ids) + 2)))
        for arc_id in overlay.graph.arcs:
            prepared = integer_arc(dense, dense.arc_indices[arc_id])
            self.assertEqual(prepared.record['id'], arc_id)
        self.assertEqual(shortest_path(overlay.graph, overlay.home, overlay.turnaround),
                         shortest_path_string(overlay.graph, overlay.home, overlay.turnaround))

    def test_exploration_overlay_and_relaxed_policy(self) -> None:
        document = fixture()
        strict = build_graph_index(document, exploration_profile(avoid_stairs=True))
        relaxed = apply_profile(strict, exploration_profile(avoid_stairs=False))
        assert strict.integer is not None and relaxed.integer is not None
        self.assertIs(strict.integer.arcs, relaxed.integer.arcs)
        self.assertEqual(strict.integer.node_indices, relaxed.integer.node_indices)
        self.assertEqual(strict.integer.arc_indices, relaxed.integer.arc_indices)
        for index in (strict, relaxed):
            overlay, start = split_start_graph(index, snap(document, 'sa', .4))
            for destination in index.nodes:
                self.assertEqual(shortest_path(overlay, start, destination),
                                 shortest_path_string(overlay, start, destination))

    def test_generated_graphs_match_string_search_with_costs_and_blocks(self) -> None:
        rng = random.Random(42)
        for _ in range(20):
            nodes = [(str(n * 7), 24.8, 60.2) for n in range(12)]
            edges = [(f'e{a}-{b}', nodes[a][0], nodes[b][0], rng.randrange(1, 20), rng.randrange(4),
                      {'highway': rng.choice(('path', 'steps')), 'oneway:foot': rng.choice(('yes', 'no'))})
                     for a in range(12) for b in range(a + 1, 12) if rng.random() < .2]
            index = build_graph_index(graph_document(nodes, edges))
            model = CostModel(ascent_cost_per_m=1.5, stairs_multiplier=2)
            for _ in range(8):
                start, destination = rng.sample(list(index.nodes), 2)
                blocked = frozenset(rng.sample(list(index.segments), min(2, len(index.segments))))
                overlap = frozenset(list(index.segments)[:3])
                actual = shortest_path(index, start, destination, model, overlap, 2, blocked)
                expected = shortest_path_string(index, start, destination, model, overlap, 2, blocked)
                self.assertEqual(actual, expected)
                if actual is not None and expected is not None:
                    self.assertEqual(actual.expanded_nodes, expected.expanded_nodes)

    def test_deadline_and_invalid_endpoints(self) -> None:
        index = build_graph_index(fixture())
        with self.assertRaises(TimeoutError):
            shortest_path(index, 's', 'd', deadline=monotonic() - 1)
        with self.assertRaises(ValueError):
            shortest_path(index, 'missing', 'd')
        self.assertEqual(shortest_path(index, 's', 'd', blocked_segments={'missing'}),
                         shortest_path(index, 's', 'd'))

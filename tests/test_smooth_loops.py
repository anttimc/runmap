"""Verify experimental smooth-loop penalties and search against a reference."""

import math
import unittest

from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from runmap.experiments.smooth_loops import (
    Settings, SpatialGraph, astar, edge_weights, penalty_fields, path_length, plan_outing, random_delaunay_graph,
)


class SmoothLoopTests(unittest.TestCase):
    def test_weights_match_scalar_penalties(self) -> None:
        graph = SpatialGraph(((3, 7), (93, 27), (3, 7), (10, -8)),
                             ((1, 2), (0, 3), (0, 3), (1, 2)))
        settings = Settings(8, radius=15, transition_width=5)
        reused = frozenset({(0, 1)})
        for discourage_right in (True, False):
            weights = edge_weights(graph, 0, 1, settings, discourage_right, reused)
            for (a, b), weight in weights.items():
                first, second = graph.points[a], graph.points[b]
                distance = math.dist(first, second)
                count = max(4, math.ceil(distance))
                penalty = 0.0
                for sample in range(count):
                    fraction = (sample + 0.5) / count
                    point = (first[0] + fraction * (second[0] - first[0]),
                             first[1] + fraction * (second[1] - first[1]))
                    radial, right = penalty_fields(point, graph.points[0], graph.points[1], settings)
                    side = right if discourage_right else 1 - right
                    penalty += (settings.centre_strength * radial + settings.side_strength * side) / count
                expected = distance * (1 + penalty + settings.reuse_strength * ((min(a, b), max(a, b)) in reused))
                self.assertAlmostEqual(weight, expected, places=10)
                self.assertEqual(weight, weights[b, a])

    def test_graph_is_reproducible_and_edges_are_symmetric(self) -> None:
        graph = random_delaunay_graph(100, 30, 20, anchors=((3, 10), (27, 10)))
        self.assertEqual(graph, random_delaunay_graph(100, 30, 20, anchors=((3, 10), (27, 10))))
        self.assertEqual(graph.points[:2], ((3, 10), (27, 10)))
        self.assertEqual(len(graph.points), 102)
        for node, neighbours in enumerate(graph.neighbours):
            self.assertTrue(neighbours)
            self.assertNotIn(node, neighbours)
            for neighbour in neighbours:
                self.assertIn(node, graph.neighbours[neighbour])

    def test_astar_matches_reference_dijkstra(self) -> None:
        graph = random_delaunay_graph(100, 30, 20, anchors=((3, 10), (27, 10)))
        for settings in (Settings(0, 0, 0), Settings(8, radius=4)):
            weights = edge_weights(graph, 0, 1, settings, True)
            rows, columns = zip(*weights)
            matrix = csr_matrix((list(weights.values()), (rows, columns)), shape=(102, 102))
            result = astar(graph, 0, 1, lambda a, b: weights[a, b])
            assert result is not None
            self.assertAlmostEqual(result.cost, float(dijkstra(matrix, indices=0)[1]))
            if settings.centre_strength == 0:
                self.assertAlmostEqual(result.cost, path_length(graph, result.path))

    def test_opposite_preferences_form_distinct_legs(self) -> None:
        graph = random_delaunay_graph(1000, 30, 30, anchors=((3, 15), (27, 15)))
        result = plan_outing(graph, 0, 1, Settings(8, radius=4, transition_width=1))
        assert result is not None
        outbound, returning = result
        self.assertEqual((outbound.path[0], outbound.path[-1]), (0, 1))
        self.assertEqual((returning.path[0], returning.path[-1]), (1, 0))
        self.assertGreater(max(graph.points[n][1] for n in outbound.path), 20)
        self.assertLess(min(graph.points[n][1] for n in returning.path), 10)
        for leg in (outbound, returning):
            for a, b in zip(leg.path, leg.path[1:]):
                self.assertIn(b, graph.neighbours[a])


if __name__ == '__main__':
    unittest.main()

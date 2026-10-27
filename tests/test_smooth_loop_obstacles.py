"""Verify obstacle handling in the experimental smooth-loop graph."""

import unittest

from runmap.experiments.smooth_loop_obstacles import intersects_rectangle, remove_obstructed_edges
from runmap.experiments.smooth_loops import Settings, SpatialGraph, plan_outing, random_delaunay_graph


class ObstacleTests(unittest.TestCase):
    def test_full_edge_intersection_includes_boundary_contact(self) -> None:
        rectangle = (1.0, 1.0, 2.0, 2.0)
        self.assertTrue(intersects_rectangle((0, 1.5), (3, 1.5), rectangle))
        self.assertTrue(intersects_rectangle((0, 0), (1, 1), rectangle))
        self.assertFalse(intersects_rectangle((0, 0), (3, 0), rectangle))
        self.assertFalse(intersects_rectangle((0, 0), (0, 3), rectangle))

    def test_wall_disconnects_and_preserves_symmetric_adjacency(self) -> None:
        graph = random_delaunay_graph(100, 10, 10, anchors=((2, 5), (8, 5)))
        filtered = remove_obstructed_edges(graph, ((4, 0, 6, 10),))
        self.assertIsNone(plan_outing(filtered, 0, 1, Settings(2)))
        for node, neighbours in enumerate(filtered.neighbours):
            for neighbour in neighbours:
                self.assertIn(node, filtered.neighbours[neighbour])
                self.assertFalse(intersects_rectangle(filtered.points[node], filtered.points[neighbour], (4, 0, 6, 10)))

    def test_unavoidable_shared_stem_remains_usable(self) -> None:
        graph = SpatialGraph(((0, 0), (2, 0), (1, 0), (1.5, 1), (1.5, -1)),
                             ((2,), (3, 4), (0, 3, 4), (1, 2), (1, 2)))
        result = plan_outing(graph, 0, 1, Settings(2, radius=0.3, transition_width=0.1))
        assert result is not None
        outbound, returning = result
        self.assertEqual(outbound.path[:2], (0, 2))
        self.assertEqual(returning.path[-2:], (2, 0))


if __name__ == "__main__":
    unittest.main()

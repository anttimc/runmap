import unittest

from runmap.routing_reference.baseline import generate_anchor_loops
from runmap.routing_reference.search import CostModel, diverse_path_pair, route_metrics, shortest_distances, shortest_path
from runmap.routing.graph import build_graph_index


def graph_document(nodes, edges, start="s"):
    node_records = [
        {"id": node_id, "longitude": longitude, "latitude": latitude}
        for node_id, longitude, latitude in nodes
    ]
    coordinates = {node[0]: [node[1], node[2]] for node in nodes}
    segments = []
    arcs = []
    for segment_id, first, second, length, ascent, tags in edges:
        segment = {
            "id": segment_id,
            "from": first,
            "to": second,
            "length_m": float(length),
            "geometry": [coordinates[first], coordinates[second]],
            "tags": dict(tags),
            "forward": True,
            "backward": True,
            "ascent_m": float(ascent),
            "descent_m": 0.0,
        }
        segments.append(segment)
        arcs.extend([
            {
                "id": f"{segment_id}:f", "segment_id": segment_id,
                "from": first, "to": second, "length_m": float(length),
                "ascent_m": float(ascent), "descent_m": 0.0,
            },
            {
                "id": f"{segment_id}:b", "segment_id": segment_id,
                "from": second, "to": first, "length_m": float(length),
                "ascent_m": 0.0, "descent_m": float(ascent),
            },
        ])
    return {
        "nodes": node_records,
        "segments": segments,
        "arcs": arcs,
        "start_snap": {"id": start},
    }


class ShortestPathTests(unittest.TestCase):
    def test_heuristic_can_reopen_a_node_with_a_better_path(self) -> None:
        graph = graph_document(
            [(node, 24.0, 60.0) for node in ("s", "a", "b", "d")],
            [
                ("sa", "s", "a", 3, 0, {"highway": "path"}),
                ("sb", "s", "b", 1, 0, {"highway": "path"}),
                ("ba", "b", "a", 1, 0, {"highway": "path"}),
                ("ad", "a", "d", 3, 0, {"highway": "path"}),
            ],
        )
        index = build_graph_index(graph)
        estimates = {"s": 5.0, "a": 0.0, "b": 4.0, "d": 0.0}

        def heuristic(node_id: str, destination: str) -> float:
            self.assertEqual(destination, "d")
            return estimates[node_id]

        result = shortest_path(index, "s", "d", heuristic=heuristic)
        self.assertEqual(result, shortest_path(index, "s", "d"))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.cost, 5.0)
        self.assertEqual(result.arc_ids, ("sb:f", "ba:f", "ad:f"))

    def test_invalid_heuristic_is_rejected(self) -> None:
        index = build_graph_index(graph_document(
            [("s", 24.0, 60.0), ("d", 24.01, 60.0)],
            [("sd", "s", "d", 10, 0, {"highway": "path"})],
        ))
        for estimate in (-1.0, float("nan"), float("inf"), 1.0):
            with self.subTest(estimate=estimate):
                def heuristic(node_id: str, destination: str) -> float:
                    return estimate

                with self.assertRaises(ValueError):
                    shortest_path(index, "s", "d", heuristic=heuristic)

    def test_reverse_distances_measure_return_reachability(self):
        graph = graph_document(
            [("s", 24.0, 60.0), ("a", 24.01, 60.0), ("b", 24.02, 60.0)],
            [
                ("sa", "s", "a", 100, 0, {"highway": "path"}),
                ("bs", "b", "s", 150, 0, {"highway": "path"}),
            ],
        )
        graph["arcs"] = [
            arc for arc in graph["arcs"]
            if arc["id"] in {"sa:f", "bs:f"}
        ]
        index = build_graph_index(graph)

        outward = shortest_distances(index, "s")
        returning = shortest_distances(index, "s", reverse=True)

        self.assertEqual(outward, {"s": 0.0, "a": 100.0})
        self.assertEqual(returning, {"s": 0.0, "b": 150.0})

    def test_ascent_cost_can_prefer_a_longer_flat_path(self):
        graph = graph_document(
            [("s", 24.0, 60.0), ("a", 24.01, 60.0), ("d", 24.02, 60.0)],
            [
                ("direct", "s", "d", 100, 10, {"highway": "path"}),
                ("flat-1", "s", "a", 60, 0, {"highway": "path"}),
                ("flat-2", "a", "d", 60, 0, {"highway": "path"}),
            ],
        )
        index = build_graph_index(graph)

        result = shortest_path(index, "s", "d", CostModel(ascent_cost_per_m=5))

        self.assertEqual(result.arc_ids, ("flat-1:f", "flat-2:f"))

    def test_return_path_avoids_outbound_segments_when_an_alternative_exists(self):
        graph = graph_document(
            [
                ("s", 24.0, 60.0), ("a", 24.01, 60.01),
                ("b", 24.01, 59.99), ("d", 24.02, 60.0),
            ],
            [
                ("sa", "s", "a", 100, 0, {"highway": "path"}),
                ("ad", "a", "d", 100, 0, {"highway": "path"}),
                ("sb", "s", "b", 120, 0, {"highway": "path"}),
                ("bd", "b", "d", 120, 0, {"highway": "path"}),
            ],
        )
        index = build_graph_index(graph)

        outbound, returning = diverse_path_pair(
            index, "s", "d", CostModel(), overlap_factor=10
        )
        metrics = route_metrics(index, outbound.arc_ids + returning.arc_ids)

        self.assertEqual(outbound.arc_ids, ("sa:f", "ad:f"))
        self.assertEqual(returning.arc_ids, ("bd:b", "sb:b"))
        self.assertEqual(metrics["distance_m"], 440)
        self.assertEqual(metrics["repeated_fraction"], 0)

    def test_do_not_cross_geometry_cuts_a_route_boundary(self):
        graph = graph_document(
            [("s", 24.0, 60.0), ("d", 24.02, 60.0)],
            [("direct", "s", "d", 100, 0, {"highway": "path"})],
        )
        graph["route_boundaries"] = [{
            "id": "boundary",
            "geometry": [[24.01, 59.99], [24.01, 60.01]],
        }]
        index = build_graph_index(graph)

        result = shortest_path(index, "s", "d")

        self.assertIsNone(result)
        self.assertEqual(index.boundary_blocked_segments, {"direct"})

    def test_grade_separated_path_can_cross_a_route_boundary(self):
        graph = graph_document(
            [("s", 24.0, 60.0), ("d", 24.02, 60.0)],
            [("underpass", "s", "d", 100, 0, {
                "highway": "cycleway", "foot": "designated",
                "tunnel": "yes", "layer": "-1",
            })],
        )
        graph["route_boundaries"] = [{
            "id": "boundary",
            "geometry": [[24.01, 59.99], [24.01, 60.01]],
        }]
        index = build_graph_index(graph)

        result = shortest_path(index, "s", "d")

        self.assertIsNotNone(result)
        self.assertEqual(index.boundary_blocked_segments, set())

    def test_route_boundary_extends_to_nearby_preparation_edge(self):
        graph = graph_document(
            [("s", 24.0, 60.0095), ("d", 24.02, 60.0095)],
            [("around-end", "s", "d", 100, 0, {"highway": "path"})],
        )
        graph["source"] = {"osm": {"bounds_wgs84": [59.99, 24.0, 60.01, 24.02]}}
        graph["route_boundaries"] = [{
            "id": "boundary",
            "geometry": [[24.01, 59.991], [24.01, 60.009]],
        }]
        index = build_graph_index(graph)

        self.assertIsNone(shortest_path(index, "s", "d"))
        self.assertEqual(index.boundary_blocked_segments, {"around-end"})


class BaselineLoopTests(unittest.TestCase):
    def test_anchor_baseline_builds_a_ten_kilometre_loop(self):
        graph = graph_document(
            [
                ("s", 24.0, 60.0), ("a", 24.01, 60.01),
                ("b", 24.01, 59.99), ("d", 24.02, 60.0),
            ],
            [
                ("sa", "s", "a", 2500, 10, {"highway": "path"}),
                ("ad", "a", "d", 2500, 10, {"highway": "path"}),
                ("sb", "s", "b", 2500, 5, {"highway": "path"}),
                ("bd", "b", "d", 2500, 5, {"highway": "path"}),
            ],
        )
        index = build_graph_index(graph)
        request = {
            "target_distance_m": 10000,
            "distance_tolerance_m": 500,
            "max_repeated_fraction": 0.1,
        }
        settings = {
            "anchor_count": 4,
            "alternative_count": 2,
            "ascent_costs_per_m": [0],
            "stairs_multiplier": 8,
            "overlap_factors": [10],
        }

        result = generate_anchor_loops(index, request, settings)

        self.assertEqual(result["anchor_count"], 1)
        self.assertEqual(result["feasible_candidate_count"], 1)
        self.assertEqual(result["alternatives"][0]["metrics"]["distance_m"], 10000)
        self.assertEqual(result["alternatives"][0]["metrics"]["repeated_fraction"], 0)
        self.assertFalse(result["alternatives"][0]["metrics"]["start_access_reversal"])
        self.assertTrue(result["alternatives"][0]["feasible"])


if __name__ == "__main__":
    unittest.main()

import unittest

from runmap.routing.face_search import _growth_chunks, generate_face_region_loops
from runmap.routing.profiles import dissolve_forbidden_separators
from runmap.routing.search import GraphIndex


def two_square_graph():
    coordinates = {
        "a": (24.000, 60.000), "b": (24.001, 60.000),
        "c": (24.002, 60.000), "d": (24.000, 60.001),
        "e": (24.001, 60.001), "f": (24.002, 60.001),
    }
    edges = [
        ("ab", "a", "b"), ("bc", "b", "c"),
        ("de", "d", "e"), ("ef", "e", "f"),
        ("ad", "a", "d"), ("be", "b", "e"), ("cf", "c", "f"),
    ]
    segments = []
    arcs = []
    for segment_id, first, second in edges:
        segment = {
            "id": segment_id,
            "from": first,
            "to": second,
            "length_m": 100.0,
            "geometry": [coordinates[first], coordinates[second]],
            "tags": {"highway": "path"},
            "forward": True,
            "backward": True,
        }
        segments.append(segment)
        arcs.extend([
            {"id": f"{segment_id}:f", "segment_id": segment_id,
             "from": first, "to": second, "length_m": 100.0,
             "ascent_m": 0.0, "descent_m": 0.0},
            {"id": f"{segment_id}:b", "segment_id": segment_id,
             "from": second, "to": first, "length_m": 100.0,
             "ascent_m": 0.0, "descent_m": 0.0},
        ])
    document = {
        "nodes": [
            {"id": node_id, "longitude": point[0], "latitude": point[1]}
            for node_id, point in coordinates.items()
        ],
        "segments": segments,
        "arcs": arcs,
        "route_boundaries": [],
        "start_snap": {"id": "a"},
    }
    topology = {"face_embedding": {
        "faces": [
            {"id": "left", "node_ids": ["a", "b", "e", "d"],
             "segment_ids": ["ab", "be", "de", "ad"],
             "area_m2": 10000.0,
             "geometry": [[24.000, 60.000], [24.001, 60.000],
                          [24.001, 60.001], [24.000, 60.001], [24.000, 60.000]]},
            {"id": "right", "node_ids": ["b", "c", "f", "e"],
             "segment_ids": ["bc", "cf", "ef", "be"],
             "area_m2": 10000.0,
             "geometry": [[24.001, 60.000], [24.002, 60.000],
                          [24.002, 60.001], [24.001, 60.001], [24.001, 60.000]]},
        ],
        "face_adjacency": [{"segment_id": "be", "faces": ["left", "right"]}],
    }}
    return GraphIndex(document), topology


class FaceRegionSearchTests(unittest.TestCase):
    def test_growth_proposes_connected_multi_face_chunks(self):
        adjacent = {
            "a": {"b", "c"},
            "b": {"a", "d"},
            "c": {"a"},
            "d": {"b"},
        }

        import random
        chunks = _growth_chunks(
            {"a"}, adjacent, maximum_size=3, proposals_per_size=10,
            rng=random.Random(4),
        )

        self.assertIn(frozenset({"b"}), chunks)
        self.assertIn(frozenset({"b", "d"}), chunks)
        self.assertNotIn(frozenset({"c", "d"}), chunks)

    def test_grows_adjacent_faces_and_extracts_one_outer_cycle(self):
        index, topology = two_square_graph()

        result = generate_face_region_loops(
            index,
            topology,
            {
                "target_distance_m": 600,
                "distance_tolerance_m": 10,
                "max_repeated_fraction": 0.1,
            },
            {
                "seed_count": 1,
                "variants_per_seed": 1,
                "max_faces": 3,
                "alternative_count": 1,
            },
        )

        self.assertEqual(result["feasible_candidate_count"], 1)
        candidate = result["alternatives"][0]
        self.assertEqual(candidate["face_count"], 2)
        self.assertEqual(set(candidate["base_face_ids"]), {"left", "right"})
        self.assertEqual(set(candidate["boundary_segment_ids"]), {
            "ab", "bc", "cf", "ef", "de", "ad",
        })
        self.assertEqual(candidate["metrics"]["distance_m"], 600)
        self.assertEqual(candidate["metrics"]["repeated_fraction"], 0)
        self.assertEqual(candidate["shape_metrics"]["concave_turn_count"], 0)
        self.assertEqual(candidate["shape_metrics"]["sharp_turn_count"], 0)

        repeated = generate_face_region_loops(
            index,
            topology,
            {
                "target_distance_m": 600,
                "distance_tolerance_m": 10,
                "max_repeated_fraction": 0.1,
            },
            {
                "seed_count": 1,
                "variants_per_seed": 1,
                "max_faces": 3,
                "alternative_count": 1,
            },
        )
        self.assertEqual(result["alternatives"], repeated["alternatives"])

    def test_forbidden_shared_edge_dissolves_two_faces(self):
        index, topology = two_square_graph()
        index.segments["be"]["tags"]["surface"] = "ground"
        profile = {"id": "street-shoes", "forbidden_surfaces": ["ground"]}

        simplified = dissolve_forbidden_separators(
            index, topology["face_embedding"], profile
        )

        self.assertEqual(simplified["summary"]["base_face_count"], 2)
        self.assertEqual(simplified["summary"]["superface_count"], 1)
        self.assertEqual(simplified["summary"]["dissolved_separator_count"], 1)
        superface = simplified["faces"][0]
        self.assertEqual(set(superface["member_face_ids"]), {"left", "right"})
        self.assertNotIn("be", superface["segment_ids"])

        result = generate_face_region_loops(
            index,
            topology,
            {
                "target_distance_m": 600,
                "distance_tolerance_m": 10,
                "max_repeated_fraction": 0.1,
            },
            {
                "seed_count": 1, "variants_per_seed": 1, "max_faces": 1,
                "alternative_count": 1, "edge_profile": profile,
            },
        )
        candidate = result["alternatives"][0]
        self.assertEqual(candidate["face_count"], 1)
        self.assertEqual(candidate["base_face_count"], 2)
        self.assertNotIn("be", candidate["boundary_segment_ids"])

    def test_avoid_stairs_dissolves_a_shared_step_edge(self):
        index, topology = two_square_graph()
        index.segments["be"]["tags"] = {"highway": "steps"}

        result = generate_face_region_loops(
            index,
            topology,
            {
                "target_distance_m": 600,
                "distance_tolerance_m": 10,
                "max_repeated_fraction": 0.1,
            },
            {
                "seed_count": 1, "variants_per_seed": 1, "max_faces": 1,
                "alternative_count": 1, "stairs": {"mode": "avoid"},
            },
        )

        self.assertEqual(result["profile_summary"]["dissolved_separator_count"], 1)
        self.assertNotIn("be", result["alternatives"][0]["boundary_segment_ids"])

    def test_stairs_can_be_allowed_or_penalized(self):
        index, topology = two_square_graph()
        index.segments["ad"]["tags"] = {"highway": "steps"}
        request = {
            "target_distance_m": 400,
            "distance_tolerance_m": 10,
            "max_repeated_fraction": 0.1,
        }
        base_settings = {
            "seed_count": 1, "variants_per_seed": 1, "max_faces": 1,
            "alternative_count": 1,
        }

        allowed = generate_face_region_loops(
            index, topology, request,
            {**base_settings, "stairs": {"mode": "allow"}},
        )["alternatives"][0]
        penalized = generate_face_region_loops(
            index, topology, request,
            {**base_settings, "stairs": {"mode": "penalize", "penalty_per_m": 10}},
        )["alternatives"][0]

        self.assertEqual(allowed["metrics"]["stair_distance_m"], 100)
        self.assertEqual(penalized["shape_score"] - allowed["shape_score"], 1000)

    def test_near_reversal_can_be_a_hard_constraint(self):
        index, topology = two_square_graph()

        candidate = generate_face_region_loops(
            index,
            topology,
            {
                "target_distance_m": 600,
                "distance_tolerance_m": 10,
                "max_repeated_fraction": 0.1,
            },
            {
                "seed_count": 1, "variants_per_seed": 1, "max_faces": 3,
                "alternative_count": 1, "stairs": {"mode": "allow"},
                "turns": {
                    "sharp_threshold_degrees": 80,
                    "maximum_sharp_turns": 0,
                },
            },
        )["alternatives"][0]

        self.assertFalse(candidate["feasible"])
        self.assertGreater(candidate["constraint_violations"]["sharp_turn_count"], 0)


if __name__ == "__main__":
    unittest.main()

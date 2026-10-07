import unittest

from runmap.routing.face_search import generate_face_region_loops
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
        self.assertEqual(set(candidate["face_ids"]), {"left", "right"})
        self.assertEqual(set(candidate["boundary_segment_ids"]), {
            "ab", "bc", "cf", "ef", "de", "ad",
        })
        self.assertEqual(candidate["metrics"]["distance_m"], 600)
        self.assertEqual(candidate["metrics"]["repeated_fraction"], 0)

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


if __name__ == "__main__":
    unittest.main()

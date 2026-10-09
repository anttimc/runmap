import unittest

from rasterio.warp import transform

from runmap.routing.cycle_topology import build_cycle_block_topology
from runmap.routing.search import GraphIndex
from runmap.routing.topology import (
    connectivity_topology,
    enumerate_faces,
    symmetric_difference_boundary,
)


def topology_graph(projected_nodes, edges):
    identifiers = list(projected_nodes)
    eastings = [projected_nodes[node_id][0] for node_id in identifiers]
    northings = [projected_nodes[node_id][1] for node_id in identifiers]
    longitudes, latitudes = transform(
        "EPSG:3067", "EPSG:4326", eastings, northings
    )
    coordinates = {
        node_id: [longitude, latitude]
        for node_id, longitude, latitude in zip(identifiers, longitudes, latitudes)
    }
    nodes = [
        {"id": node_id, "longitude": coordinates[node_id][0], "latitude": coordinates[node_id][1]}
        for node_id in identifiers
    ]
    segments = []
    arcs = []
    for edge in edges:
        segment_id, first, second = edge[:3]
        tags = edge[3] if len(edge) > 3 else {"highway": "path"}
        length = (
            (projected_nodes[second][0] - projected_nodes[first][0]) ** 2
            + (projected_nodes[second][1] - projected_nodes[first][1]) ** 2
        ) ** 0.5
        segments.append({
            "id": segment_id,
            "from": first,
            "to": second,
            "length_m": length,
            "geometry": [coordinates[first], coordinates[second]],
            "tags": dict(tags),
            "forward": True,
            "backward": True,
        })
        arcs.extend([
            {
                "id": f"{segment_id}:f", "segment_id": segment_id,
                "from": first, "to": second, "length_m": length,
            },
            {
                "id": f"{segment_id}:b", "segment_id": segment_id,
                "from": second, "to": first, "length_m": length,
            },
        ])
    return GraphIndex({
        "nodes": nodes,
        "segments": segments,
        "arcs": arcs,
        "route_boundaries": [],
    })


class ConnectivityTopologyTests(unittest.TestCase):
    def test_triangle_with_tail_separates_bridge_core_and_articulation(self):
        index = topology_graph(
            {
                "1": (375000, 6675000), "2": (375100, 6675000),
                "3": (375050, 6675100), "4": (375050, 6675200),
            },
            [("12", "1", "2"), ("23", "2", "3"), ("31", "3", "1"), ("34", "3", "4")],
        )

        result = connectivity_topology(index)

        self.assertEqual(result["bridge_segment_ids"], {"34"})
        self.assertEqual(result["articulation_node_ids"], {"3"})
        self.assertEqual(result["core_segment_ids"], {"12", "23", "31"})
        self.assertEqual(len(result["cyclic_components"]), 1)
        self.assertEqual(result["cyclic_components"][0]["cycle_rank"], 1)
        self.assertEqual(len(result["cyclic_blocks"]), 1)

    def test_self_loop_is_a_cyclic_biconnected_block(self):
        index = topology_graph(
            {"1": (375000, 6675000)},
            [("loop", "1", "1")],
        )

        result = connectivity_topology(index)

        self.assertEqual(result["bridge_segment_ids"], set())
        self.assertEqual(result["core_segment_ids"], {"loop"})
        self.assertEqual(len(result["cyclic_blocks"]), 1)
        self.assertEqual(result["cyclic_blocks"][0]["cycle_rank"], 1)


class HalfEdgeFaceTests(unittest.TestCase):
    def test_adjacent_faces_share_and_cancel_the_middle_edge(self):
        nodes = {
            "a": (375000, 6675000), "b": (375100, 6675000), "c": (375200, 6675000),
            "d": (375000, 6675100), "e": (375100, 6675100), "f": (375200, 6675100),
        }
        index = topology_graph(
            nodes,
            [
                ("ab", "a", "b"), ("bc", "b", "c"),
                ("de", "d", "e"), ("ef", "e", "f"),
                ("ad", "a", "d"), ("be", "b", "e"), ("cf", "c", "f"),
            ],
        )
        connectivity = connectivity_topology(index)

        embedding = enumerate_faces(
            index, connectivity, [374900, 6674900, 375300, 6675200]
        )

        self.assertEqual(len(embedding["faces"]), 2)
        self.assertEqual(len(embedding["face_adjacency"]), 1)
        shared = embedding["face_adjacency"][0]["segment_id"]
        boundary = symmetric_difference_boundary(
            [face["id"] for face in embedding["faces"]], embedding
        )
        self.assertEqual(shared, "be")
        self.assertNotIn("be", boundary)
        self.assertEqual(boundary, {"ab", "bc", "cf", "ef", "de", "ad"})
        for face in embedding["faces"]:
            self.assertAlmostEqual(face["area_m2"], 10000, delta=1)

    def test_tunnel_corridor_retains_connections_between_planar_faces(self):
        nodes = {
            "a": (375000, 6675000), "b": (375100, 6675000),
            "c": (375100, 6675100), "d": (375000, 6675100),
            "e": (375200, 6675000), "f": (375300, 6675000),
            "g": (375300, 6675100), "h": (375200, 6675100),
            "x": (375125, 6675000), "y": (375175, 6675000),
        }
        index = topology_graph(
            nodes,
            [
                ("ab", "a", "b"), ("bc", "b", "c"),
                ("cd", "c", "d"), ("da", "d", "a"),
                ("ef", "e", "f"), ("fg", "f", "g"),
                ("gh", "g", "h"), ("he", "h", "e"),
                ("left-approach", "b", "x"),
                ("tunnel", "x", "y", {"highway": "path", "tunnel": "yes", "layer": "-1"}),
                ("right-approach", "y", "e"),
            ],
        )
        connectivity = connectivity_topology(index)

        embedding = enumerate_faces(
            index, connectivity, [374900, 6674900, 375400, 6675200]
        )

        self.assertEqual(len(embedding["faces"]), 2)
        self.assertEqual(len(embedding["connector_corridors"]), 1)
        corridor = embedding["connector_corridors"][0]
        self.assertEqual(corridor["structural_segment_ids"], ["tunnel"])
        self.assertEqual(
            set(corridor["approach_segment_ids"]),
            {"left-approach", "right-approach"},
        )
        self.assertEqual(len(corridor["attached_face_ids"]), 2)
        self.assertEqual(corridor["structure_reasons"], ["layer:-1", "tunnel"])


class CycleBlockTopologyTests(unittest.TestCase):
    @staticmethod
    def _topology(index):
        return {"face_embedding": {"segment_ids": sorted(index.segments)}}

    def test_articulation_splits_cycles_into_complete_block_embeddings(self):
        index = topology_graph(
            {
                "a": (375000, 6675000), "b": (375100, 6675000),
                "c": (375100, 6675100), "d": (375000, 6675100),
                "e": (375200, 6675100), "f": (375200, 6675200),
                "g": (375100, 6675200),
            },
            [
                ("ab", "a", "b"), ("bc", "b", "c"),
                ("cd", "c", "d"), ("da", "d", "a"),
                ("ce", "c", "e"), ("ef", "e", "f"),
                ("fg", "f", "g"), ("gc", "g", "c"),
            ],
        )

        result = build_cycle_block_topology(
            index, self._topology(index), stairs={"mode": "allow"}
        )

        self.assertEqual(result["summary"]["cyclic_block_count"], 2)
        self.assertEqual(result["summary"]["valid_block_count"], 2)
        self.assertEqual(result["summary"]["selectable_face_count"], 2)
        for block in result["blocks"]:
            self.assertIsNotNone(block["outer_face_id"])
            self.assertEqual(len(block["faces"]), 2)
            self.assertEqual(len(block["selectable_face_ids"]), 1)
            self.assertTrue(block["valid_for_dual"])
            self.assertTrue(all(
                len(item["faces"]) == 2 and None not in item["faces"]
                for item in block["segment_incidences"]
            ))

    def test_disconnected_cycles_have_independent_outer_faces(self):
        index = topology_graph(
            {
                "a": (375000, 6675000), "b": (375100, 6675000),
                "c": (375100, 6675100), "d": (375000, 6675100),
                "e": (375300, 6675000), "f": (375400, 6675000),
                "g": (375400, 6675100), "h": (375300, 6675100),
            },
            [
                ("ab", "a", "b"), ("bc", "b", "c"),
                ("cd", "c", "d"), ("da", "d", "a"),
                ("ef", "e", "f"), ("fg", "f", "g"),
                ("gh", "g", "h"), ("he", "h", "e"),
            ],
        )

        result = build_cycle_block_topology(
            index, self._topology(index), stairs={"mode": "allow"}
        )

        self.assertEqual(len(result["blocks"]), 2)
        self.assertEqual(
            len({block["outer_face_id"] for block in result["blocks"]}), 2
        )
        self.assertTrue(all(block["valid_for_dual"] for block in result["blocks"]))

    def test_profile_filtering_recomputes_block_and_merges_faces(self):
        index = topology_graph(
            {
                "a": (375000, 6675000), "b": (375100, 6675000),
                "c": (375200, 6675000), "d": (375000, 6675100),
                "e": (375100, 6675100), "f": (375200, 6675100),
            },
            [
                ("ab", "a", "b"), ("bc", "b", "c"),
                ("de", "d", "e"), ("ef", "e", "f"),
                ("ad", "a", "d"),
                ("be", "b", "e", {"highway": "path", "surface": "ground"}),
                ("cf", "c", "f"),
            ],
        )

        result = build_cycle_block_topology(
            index,
            self._topology(index),
            edge_profile={"id": "test", "forbidden_surfaces": ["ground"]},
            stairs={"mode": "allow"},
        )

        self.assertEqual(result["forbidden_segment_reasons"], {
            "be": "surface:ground"
        })
        self.assertNotIn("be", result["allowed_segment_ids"])
        self.assertEqual(result["summary"]["cyclic_block_count"], 1)
        block = result["blocks"][0]
        self.assertTrue(block["valid_for_dual"])
        self.assertEqual(len(block["selectable_face_ids"]), 1)
        bounded = next(face for face in block["faces"] if face["selectable"])
        self.assertAlmostEqual(bounded["area_m2"], 20000, delta=1)


if __name__ == "__main__":
    unittest.main()

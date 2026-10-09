import unittest

from runmap.routing.cycle_catalog import _boundary_overlap, generate_cycle_catalog
from tests.test_face_search import two_square_graph


class CycleCatalogTests(unittest.TestCase):
    def test_composes_disjoint_adjacent_cycles_from_a_root(self):
        index, topology = two_square_graph()

        result = generate_cycle_catalog(
            index,
            topology,
            {"stairs": {"mode": "allow"}, "growth": {"jitter": 0}},
            {
                "target_cycle_lengths_m": [400],
                "length_tolerance_fraction": 0.01,
                "seed_count": 2,
                "minimum_seed_spacing_m": 1,
                "variants_per_seed": 1,
                "candidates_per_variant": 1,
                "maximum_cycles_per_band": 2,
                "maximum_faces": 2,
                "composition": {
                    "source_band_m": 400,
                    "root_count": 1,
                    "maximum_modules": 2,
                    "beam_width_per_root": 2,
                    "minimum_shared_boundary_m": 10,
                },
            },
        )

        composition = result["composition"]
        self.assertEqual(composition["composition_count"], 1)
        self.assertEqual(composition["level_summary"], [{
            "module_count": 2,
            "composition_count": 1,
        }])
        merged = composition["cycles"][0]
        self.assertEqual(merged["metrics"]["distance_m"], 600)
        self.assertEqual(merged["module_count"], 2)
        self.assertEqual(merged["shared_boundary_m"], 100)
        self.assertEqual(set(merged["base_face_ids"]), {"left", "right"})
        self.assertEqual(len(merged["source"]["module_candidate_ids"]), 2)

    def test_one_growth_traversal_emits_multiple_length_bands(self):
        index, topology = two_square_graph()

        result = generate_cycle_catalog(
            index,
            topology,
            {"stairs": {"mode": "allow"}, "growth": {"jitter": 0}},
            {
                "target_cycle_lengths_m": [400, 600],
                "length_tolerance_fraction": 0.01,
                "seed_count": 1,
                "variants_per_seed": 1,
                "candidates_per_variant": 1,
                "maximum_cycles_per_band": 1,
                "maximum_faces": 3,
                "coarse_perimeter_bucket_m": 200,
            },
        )

        self.assertEqual(result["multiband_traversal_count"], 1)
        self.assertEqual(
            [item["selected_cycle_count"] for item in result["band_summary"]],
            [1, 1],
        )
        self.assertEqual(
            {cycle["metrics"]["distance_m"] for cycle in result["cycles"]},
            {400, 600},
        )

    def test_overlap_is_weighted_by_physical_length(self):
        overlap = _boundary_overlap(
            {"shared", "short-a"},
            {"shared", "short-b"},
            {"shared": 100.0, "short-a": 1.0, "short-b": 1.0},
        )

        self.assertAlmostEqual(overlap, 100 / 102)

    def test_generates_a_reusable_cycle_without_home_access(self):
        index, topology = two_square_graph()

        result = generate_cycle_catalog(
            index,
            topology,
            {
                "stairs": {"mode": "allow"},
                "turns": {
                    "sharp_threshold_degrees": 170,
                    "maximum_sharp_turns": 0,
                },
                "growth": {
                    "beam_width": 2,
                    "maximum_chunk_size": 2,
                    "chunk_proposals_per_size": 4,
                    "shape_weight": 1,
                    "near_reversal_weight": 750,
                    "jitter": 0,
                },
            },
            {
                "target_cycle_lengths_m": [600],
                "length_tolerance_fraction": 0.01,
                "spatial_seed_count": 1,
                "variants_per_seed": 1,
                "candidates_per_variant": 1,
                "maximum_cycles_per_band": 1,
                "maximum_faces": 3,
            },
        )

        self.assertEqual(result["selected_cycle_count"], 1)
        self.assertEqual(
            len(result["candidate_pool"]), result["generated_cycle_count"]
        )
        self.assertEqual(result["selected_cycle_ids"], ["cycle-1"])
        cycle = result["cycles"][0]
        self.assertIn("candidate_id", cycle)
        self.assertEqual(cycle["metrics"]["distance_m"], 600)
        self.assertEqual(len(cycle["boundary_arc_ids"]), 6)
        self.assertEqual(len(cycle["orientations"]), 2)
        self.assertGreaterEqual(len(cycle["portals"]), 1)
        self.assertEqual(cycle["portals"][0]["outward_distance_m"], 0)
        self.assertEqual(cycle["portals"][0]["return_distance_m"], 0)
        self.assertEqual(set(cycle["base_face_ids"]), {"left", "right"})
        self.assertEqual(cycle["shape_metrics"]["sharp_turn_count"], 0)
        self.assertGreater(cycle["shape_metrics"]["isoperimetric_quotient"], 0)
        self.assertGreater(cycle["shape_metrics"]["convexity_ratio"], 0)


if __name__ == "__main__":
    unittest.main()

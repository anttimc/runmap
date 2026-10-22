"""Spatial packing preserves candidate references and policy isolation."""

import unittest

from runmap.paths.spatial import filter_cells, pack_cells


class SpatialTests(unittest.TestCase):
    def test_packing_detaches_and_preserves_order_and_duplicates(self) -> None:
        source = {(0, 1): [4, 2, 4], (1, 1): []}
        packed = pack_cells(source)
        source[(0, 1)].append(9)
        self.assertEqual(list(packed[(0, 1)]), [4, 2, 4])
        self.assertEqual(list(packed[(1, 1)]), [])
        with self.assertRaises(TypeError):
            packed[(0, 1)][0] = 7

    def test_filter_shares_only_unchanged_readonly_rows(self) -> None:
        packed = pack_cells({(0, 0): [1, 2, 1], (0, 1): [2], (0, 2): []})
        filtered = filter_cells(packed, {2})
        self.assertEqual(list(filtered[(0, 0)]), [2])
        self.assertIs(filtered[(0, 1)], packed[(0, 1)])
        self.assertIs(filtered[(0, 2)], packed[(0, 2)])
        self.assertEqual(list(packed[(0, 0)]), [1, 2, 1])
        self.assertTrue(filtered[(0, 0)].readonly)

    def test_filter_accepts_unpacked_fixture_rows(self) -> None:
        filtered = filter_cells({(0, 0): [3, 1, 3]}, {3})
        self.assertEqual(list(filtered[(0, 0)]), [3, 3])

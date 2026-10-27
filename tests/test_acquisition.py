"""Verify bounded elevation acquisition requests and input validation."""

import unittest
from urllib.parse import parse_qs, urlsplit

from runmap.sources.mml import Bounds, coverage_url


class AcquisitionTests(unittest.TestCase):
    def test_request_preserves_both_axes_and_native_resolution(self):
        query = parse_qs(urlsplit(coverage_url(Bounds(374000, 6676000, 380000, 6682000))).query)
        self.assertEqual(query["SUBSET"], ["E(374000,380000)", "N(6676000,6682000)"])
        self.assertEqual(query["CoverageID"], ["korkeusmalli_2m"])
        self.assertNotIn("api-key", query)
        self.assertNotIn("SCALEFACTOR", query)

    def test_rejects_invalid_or_oversize_requests(self):
        for values in ((0, 0, 10002, 100), (0, 0, 0, 100),
                       (0, 0, float("nan"), 100), (1, 0, 100, 100)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Bounds(*values)


if __name__ == "__main__":
    unittest.main()

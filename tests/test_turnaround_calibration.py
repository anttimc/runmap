"""Residual ranges retain individual orientations and shipped calibration provenance."""

import json
from pathlib import Path
import unittest

import numpy as np

from runmap.experiments.calibrate_turnaround import residual_knots, weighted_quantile
from runmap.experiments.plot_loop_width_sweep import WidthRow
from runmap.routing.turnaround_calibration import COEFFICIENTS, RESIDUAL_KNOTS, SAMPLE_SHA256


class TurnaroundCalibrationTests(unittest.TestCase):
    def test_range_includes_orientation_variation_when_mean_error_is_zero(self) -> None:
        rows = [WidthRow("start", 1000, 0, .6, 1000, 2000, 3000, 1000, 3000, 0)]
        knot = residual_knots(rows, np.array([3000.]))[0]
        self.assertEqual((knot.lower, knot.upper, knot.routes), (-.5, .5, 2))

    def test_quantiles_reject_invalid_weights(self) -> None:
        self.assertEqual(weighted_quantile(np.array([1., 2., 3.]), np.ones(3), .5), 2)
        with self.assertRaises(ValueError):
            weighted_quantile(np.array([1.]), np.array([0.]), .1)

    def test_shipped_constants_match_the_preserved_calibration_record(self) -> None:
        report = json.loads(Path("docs/archive/history/61-cubic-turnaround-calibration.json").read_text())
        self.assertEqual(tuple(report["coefficients"]), COEFFICIENTS)
        self.assertEqual(report["sample_sha256"], SAMPLE_SHA256)
        self.assertEqual(tuple((knot["width"], knot["lower"], knot["upper"]) for knot in report["knots"]), RESIDUAL_KNOTS)

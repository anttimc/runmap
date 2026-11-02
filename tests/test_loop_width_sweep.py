"""Protect paired width comparisons and the meaning of plotted variation."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from test_path_api import fixture
from runmap.api.paths import load_path_data
from runmap.api.runs import RunRequest
from runmap.api.sqlite_runs import RunAreaLimits
from runmap.experiments.loop_width_sweep import SweepConfig, SweepPoint, SweepSample, sweep_points, validate_sweep
from runmap.experiments.plot_loop_width_sweep import distribution, matched_rows
from runmap.paths.graph import write_graph_outputs
from runmap.routing.geometry import projected_points
from runmap.routing_reference.api import generate_run


class LoopWidthSweepTests(unittest.TestCase):
    def test_radius_bearings_and_widths_share_endpoint_coordinates(self) -> None:
        config = SweepConfig(radii_m=(1500,), widths=(0, .18), bearings=4)
        points = list(sweep_points(config.starts[0], config))
        home = projected_points([[config.starts[0].longitude, config.starts[0].latitude]])[0]
        for i in range(0, len(points), 2):
            point = points[i]
            self.assertEqual((point.longitude, point.latitude), (points[i+1].longitude, points[i+1].latitude))
            xy = projected_points([[point.longitude, point.latitude]])[0]
            self.assertAlmostEqual(float(np.linalg.norm(np.subtract(xy, home))), 1500, places=5)
        self.assertEqual([point.bearing_deg for point in points[::2]], [0, 90, 180, 270])

    def test_validation_requires_zero_baseline_and_ordered_unique_widths(self) -> None:
        for config in (SweepConfig(widths=(.18, .3)), SweepConfig(widths=(0, .3, .18)),
                       SweepConfig(radii_m=(1500, 1500)), SweepConfig(bearings=2),
                       SweepConfig(workers=3), SweepConfig(limits=RunAreaLimits(max_segments=0)),
                       SweepConfig(widths=(0, float('nan')))):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_sweep(config)

    def test_matched_cohort_excludes_failed_positions_at_every_width(self) -> None:
        with TemporaryDirectory() as directory:
            graph = fixture()
            graph["segments"][0]["backward"] = True
            graph["arcs"].append({"id": "s:b", "segment_id": "s", "from": "2", "to": "1", "length_m": 220})
            path = write_graph_outputs(graph, Path(directory))["graph"]
            data = load_path_data(path)
            response = generate_run(data, RunRequest(dataset_id=data.dataset_id,
                home={"longitude": 24.801, "latitude": 60.2},
                turnaround={"longitude": 24.803, "latitude": 60.2}))
            candidates = [candidate.model_copy(update={"distance_m": distance})
                          for candidate, distance in zip(response.candidates, (600, 1000))]
            response = response.model_copy(update={"candidates": candidates})
            zero = response.model_copy(update={"candidates": [candidates[0].model_copy(update={"distance_m": 500})]})
            p = SweepPoint(0, "test", 1500, 0, 0, 24.803, 60.2)
            samples = [SweepSample(p, zero, .1, None),
                       SweepSample(replace(p, sample_id=1, width=.18), response, .1, None),
                       SweepSample(replace(p, sample_id=2, bearing_deg=90), zero, .1, None),
                       SweepSample(replace(p, sample_id=3, bearing_deg=90, width=.18),
                                   response.model_copy(update={"status": "deadline"}), .1, None)]
            rows, excluded = matched_rows(samples, (0, .18))
            self.assertEqual(excluded, {"incomplete_or_failed_position": 1})
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].mean_length_m, 500)
            self.assertEqual(rows[0].error_pct, 0)
            self.assertEqual(rows[1].mean_length_m, 800)
            self.assertEqual(rows[1].side_gap_m, 400)
            with self.assertRaisesRegex(ValueError, "Incomplete or duplicate"):
                matched_rows(samples[:-1], (0, .18))

    def test_spread_is_population_variation_not_standard_error(self) -> None:
        stats = distribution([1000, 3000])
        self.assertEqual(stats['mean'], 2000)
        self.assertEqual(stats['std'], 1000)
        self.assertEqual(stats['p10'], 1200)
        self.assertEqual(stats['p90'], 2800)

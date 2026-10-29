"""Protect grid geometry, candidate identity, and missing-result semantics."""

from dataclasses import asdict, replace
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from test_path_api import fixture
from runmap.api.paths import load_path_data
from runmap.api.runs import RunRequest
from runmap.experiments.loop_length_grid import GridConfig, GridSample, iter_grid, iter_samples, validate_config
from runmap.experiments.plot_loop_length_grid import length_cube, observations, plot_experiment
from runmap.paths.graph import write_graph_outputs
from runmap.routing.geometry import projected_points
from runmap.routing_reference.api import generate_run


class LoopLengthGridTests(unittest.TestCase):
    def test_bearing_and_lateral_axes_use_metric_grid_coordinates(self) -> None:
        for bearing in (0.0, 90.0):
            config = GridConfig(bearing_deg=bearing, forward_count=2, lateral_count=2, widths=(0.0, .18))
            points = list(iter_grid(config))
            self.assertEqual([point.sample_id for point in points], list(range(8)))
            self.assertEqual((points[0].longitude, points[0].latitude),
                             (points[1].longitude, points[1].latitude))
            home, first = projected_points([config.home, (points[0].longitude, points[0].latitude)])
            expected = (1500, 300) if bearing == 0 else (300, -1500)
            np.testing.assert_allclose(np.subtract(first, home), expected, atol=1e-5)

    def test_invalid_grid_is_rejected_before_sampling(self) -> None:
        invalid = (GridConfig(forward_count=1), GridConfig(widths=()), GridConfig(widths=(.18, 0)),
                   GridConfig(widths=(.18, .18)), GridConfig(widths=(float("nan"),)),
                   GridConfig(widths=(.8,)), GridConfig(timeout_s=0), GridConfig(snap_radius_m=201),
                   GridConfig(forward_m=(0, 100)), GridConfig(lateral_m=(10, -10)),
                   GridConfig(home=(200, 60)), GridConfig(bearing_deg=float("inf")))
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_config(config)

    def test_incomplete_run_is_rejected_before_reading_partial_gzip(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "manifest.json").write_text('{"complete": false}')
            (output / "samples.jsonl.gz").write_bytes(b"unfinished gzip")
            with self.assertRaisesRegex(ValueError, "Sampling is incomplete"):
                plot_experiment(output)

    def test_saved_samples_preserve_both_candidates_and_failure_status(self) -> None:
        with TemporaryDirectory() as directory:
            graph = fixture()
            graph["segments"][0]["backward"] = True
            graph["arcs"].append({"id": "s:b", "segment_id": "s", "from": "2", "to": "1", "length_m": 220})
            paths = write_graph_outputs(graph, directory)
            data = load_path_data(paths["graph"])
            response = generate_run(data, RunRequest(dataset_id=data.dataset_id,
                home={"longitude": 24.801, "latitude": 60.2},
                turnaround={"longitude": 24.803, "latitude": 60.2}))
            point = next(iter_grid(GridConfig(forward_count=2, lateral_count=2, widths=(.18,))))
            sample = GridSample(point, response, .25)
            path = Path(directory) / "samples.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                stream.write(json.dumps({"point": asdict(point), "wall_s": sample.wall_s,
                                         "response": response.model_dump(mode="json")}) + "\n")
            self.assertEqual(list(iter_samples(path)), [sample])
            rows = observations(sample)
            self.assertEqual([row.side for row in rows], ["left", "right"])
            self.assertTrue(all(row.distance_m is not None and row.route_sha256 for row in rows))
            self.assertTrue(all(row.wall_ms == 250 for row in rows))
            self.assertAlmostEqual(length_cube(rows, "left")[0, 0, 0], response.candidates[0].distance_m)
            # Even a partial candidate from an unfinished evaluation is excluded.
            partial = observations(replace(sample, response=response.model_copy(update={"status": "deadline"})))
            self.assertTrue(np.isnan(length_cube(partial, "left")).all())
            failed = observations(replace(sample, response=response.model_copy(update={
                "status": "no_eligible_snap", "candidates": [], "turnaround": None})))
            self.assertEqual(len(failed), 1)
            self.assertIsNone(failed[0].distance_m)
            self.assertIsNone(failed[0].snap_offset_m)
            self.assertTrue(np.isnan(length_cube(failed, "right")).all())
            error = observations(replace(sample, response=None, error="ValueError: invalid endpoint"))
            self.assertEqual(error[0].status, "planner_error")
            self.assertEqual(error[0].error, "ValueError: invalid endpoint")
            self.assertIsNone(error[0].distance_m)
            self.assertTrue(np.isnan(length_cube(error, "left")).all())
            # The single zero-width route is a common baseline, not a missing side.
            zero = [replace(rows[0], width=0)]
            np.testing.assert_array_equal(length_cube(zero, "left"), length_cube(zero, "right"))


if __name__ == "__main__":
    unittest.main()

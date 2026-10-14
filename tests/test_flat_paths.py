"""Suppress short flat patches without treating missing terrain as a connection."""

import unittest

from runmap.paths.flat import GradeInterval, flat_run_thresholds


class FlatPathTests(unittest.TestCase):
    def test_many_short_samples_form_a_fifty_metre_run(self) -> None:
        intervals = [GradeInterval(i * 2, (i + 1) * 2, (-1) ** i * 3) for i in range(25)]
        self.assertEqual(flat_run_thresholds(intervals), [3] * 25)
        self.assertEqual(flat_run_thresholds(intervals[:-1]), [None] * 24)

    def test_threshold_can_join_patches_across_a_measured_hill(self) -> None:
        intervals = [GradeInterval(0, 20, 1), GradeInterval(20, 30, -4), GradeInterval(30, 50, 2)]
        self.assertEqual(flat_run_thresholds(intervals), [4, 4, 4])

    def test_short_patch_stays_excluded_beside_a_long_run(self) -> None:
        intervals = [GradeInterval(0, 50, 2), GradeInterval(50, 60, 12), GradeInterval(60, 90, 1)]
        self.assertEqual(flat_run_thresholds(intervals), [2, None, None])

    def test_missing_terrain_and_chainage_gaps_break_runs(self) -> None:
        missing = [GradeInterval(0, 30, 1), GradeInterval(30, 35, None), GradeInterval(35, 65, 1)]
        gap = [GradeInterval(0, 30, 1), GradeInterval(35, 65, 1)]
        self.assertEqual(flat_run_thresholds(missing), [None] * 3)
        self.assertEqual(flat_run_thresholds(gap), [None] * 2)

    def test_threshold_rounds_up_to_supported_slider_step(self) -> None:
        self.assertEqual(flat_run_thresholds([GradeInterval(0, 50, -2.1)]), [2.5])
        self.assertEqual(flat_run_thresholds([GradeInterval(0, 50, 0)]), [1])

    def test_separate_segments_do_not_join(self) -> None:
        self.assertEqual(flat_run_thresholds([GradeInterval(0, 30, 1)]), [None])
        self.assertEqual(flat_run_thresholds([GradeInterval(0, 30, -1)]), [None])

    def test_empty_and_invalid_length(self) -> None:
        self.assertEqual(flat_run_thresholds([]), [])
        for length in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                flat_run_thresholds([], length)

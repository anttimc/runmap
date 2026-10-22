"""Packed display metadata preserves detached interval views."""

from dataclasses import replace
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from runmap.api.paths import load_path_data
from runmap.paths.display import DisplayIntervals, pack_display_intervals
from runmap.paths.graph import write_graph_outputs
from test_path_api import fixture


class DisplayStorageTests(unittest.TestCase):
    def test_empty_segments_do_not_shift_interval_addresses(self) -> None:
        with TemporaryDirectory() as directory:
            data = load_path_data(write_graph_outputs(fixture(), directory)["graph"])
        offsets = np.array([0, 0, 4, 4], dtype=np.int64)
        offsets.setflags(write=False)
        measurements = replace(data.measurements, interval_offsets=offsets,
                               segment_ids=("empty-first", "s", "empty-last"))
        source = [replace(item, segment=1) for item in data.features]
        packed = pack_display_intervals(measurements, source)
        self.assertEqual(list(packed), source)
        empty_offsets = np.zeros(4, dtype=np.int64)
        empty_offsets.setflags(write=False)
        empty = pack_display_intervals(replace(measurements, interval_offsets=empty_offsets), [])
        self.assertEqual(list(empty), [])

    def test_views_slices_and_ownership(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_graph_outputs(fixture(), directory)
            data = load_path_data(paths["graph"])
        source = [replace(item, flat_min_grade_50m=value) for item, value in
                  zip(data.features, (None, 0.0, 3.25, -0.0))]
        packed = pack_display_intervals(data.measurements, source)
        self.assertIs(packed.offsets.obj, data.measurements.interval_offsets)
        self.assertEqual(tuple(packed), tuple(source))
        self.assertEqual(packed[::-1], tuple(source[::-1]))
        self.assertEqual(packed[-1], source[-1])
        source[0] = replace(source[0], flat_min_grade_50m=9)
        self.assertIsNone(packed[0].flat_min_grade_50m)
        for buffer in (packed.offsets, packed.flat_thresholds, packed.present):
            self.assertTrue(buffer.readonly)
            with self.assertRaises(TypeError):
                buffer[0] = 1
        for index in (len(packed), -len(packed)-1):
            with self.assertRaises(IndexError):
                packed[index]

    def test_rejects_incompatible_interval_layouts(self) -> None:
        with TemporaryDirectory() as directory:
            data = load_path_data(write_graph_outputs(fixture(), directory)["graph"])
        source = list(data.features)
        self.assertIsInstance(data.features, DisplayIntervals)
        for invalid in (source[:-1], list(reversed(source)),
                        [source[0], replace(source[1], surface_class="other"), *source[2:]]):
            with self.assertRaises(ValueError):
                pack_display_intervals(data.measurements, invalid)

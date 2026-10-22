"""Benchmarks distinguish full requests from raw numeric-buffer access."""

from pathlib import Path
from tempfile import TemporaryDirectory
import tracemalloc
import unittest

from rasterio.warp import transform

from runmap.api import sqlite_runs
from runmap.experiments.benchmark_numeric_chunks import probe_numeric_window
from runmap.experiments.benchmark_run_latency import measure_worker
from runmap.experiments.benchmark_native_routing import benchmark_native_routing
from runmap.paths.database import export_network_database
from runmap.paths.numeric_database import export_numeric_database
from test_database_features import BOUNDS
from test_path_database import publish_fixture


class RoutingBenchmarkTests(unittest.TestCase):
    def test_native_benchmark_compares_run_costs_and_labels_preparation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            database, numeric = root / "network.sqlite", root / "numeric.sqlite"
            export_network_database(source, database)
            export_numeric_database(database, numeric)
            lon, lat = transform("EPSG:3067", "EPSG:4326", [375025, 375075], [6675000, 6675000])
            report = benchmark_native_routing(database, numeric, BOUNDS,
                home=(lon[0], lat[0]), turnaround=(lon[1], lat[1]), repeats=2, compare_reference=True)
            self.assertTrue(report["deterministic"])
            self.assertTrue(report["comparison"]["equivalent_costs"])
            self.assertEqual(report["outcomes"], ("complete", "complete"))
            self.assertEqual(len(report["native_run_ms"]), 2)
            self.assertGreater(report["native_index_ms"], 0)
            self.assertGreater(report["distance_reached_nodes"], 0)

    def test_full_request_phases_preserve_results_and_restore_instrumented_functions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            database = root / "network.sqlite"
            export_network_database(source, database)
            lon, lat = transform("EPSG:3067", "EPSG:4326", [375025, 375075], [6675000, 6675000])
            original_snap = sqlite_runs._snap
            report = measure_worker(database, (lon[0], lat[0]), (lon[1], lat[1]), repeats=2)
            self.assertIs(sqlite_runs._snap, original_snap)
            self.assertTrue(report["equivalent"])
            self.assertEqual(report["first_request"]["status"], "complete")
            candidate = report["first_request"]["candidates"][0]
            self.assertAlmostEqual(candidate["distance_m"],
                                   candidate["outbound_distance_m"] + candidate["returning_distance_m"])
            self.assertGreater(candidate["expanded_nodes"], 0)
            self.assertEqual(report["first_request"]["phase_calls"]["snap"], 2)
            self.assertEqual(report["first_request"]["phase_calls"]["prepare"], 1)
            for phase in ("topology", "features", "index", "search", "render", "json_serialization"):
                self.assertIn(phase, report["first_request"]["phases_ms"])
            self.assertEqual(len(report["repeated_requests"]), 2)
            self.assertLessEqual(report["repeated_summary"]["p50_ms"], report["repeated_summary"]["p99_ms"])

    def test_full_request_benchmark_applies_explicit_limits(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            database = root / "network.sqlite"
            export_network_database(source, database)
            lon, lat = transform("EPSG:3067", "EPSG:4326", [375025, 375075], [6675000, 6675000])
            report = measure_worker(database, (lon[0], lat[0]), (lon[1], lat[1]), repeats=1,
                                    limits=sqlite_runs.RunAreaLimits(max_segments=1, max_retries=0))
            self.assertEqual(report["max_segments"], 1)
            self.assertEqual(report["max_retries"], 0)
            for sample in [report["first_request"], *report["repeated_requests"]]:
                self.assertEqual(sample["status"], "area_limited")
                self.assertEqual(sample["search"]["area_limited_reason"], "maximum_segments")
                self.assertFalse(sample["candidates"])

    def test_numeric_probe_counts_exact_segments_and_separates_allocation_measurement(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            database, numeric = root / "network.sqlite", root / "numeric.sqlite"
            export_network_database(source, database)
            export_numeric_database(database, numeric)
            report = probe_numeric_window(numeric, BOUNDS, repeats=2, trace_allocations=True)
            self.assertFalse(tracemalloc.is_tracing())
            self.assertEqual(report["selected_segments"], 3)
            self.assertGreater(report["buffer_bytes"], 0)
            self.assertEqual(len(report["repeated_load_ms"]), 2)
            self.assertGreater(report["allocations"]["traced_peak_bytes"], 0)
            self.assertNotIn("search", report)

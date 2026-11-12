"""Overlapping searches reuse topology without coupling their request state."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
from unittest.mock import patch

import numpy as np

from runmap import _routing_native
from runmap.api.numeric_data import NumericRoutingData
from runmap.paths.database import export_network_database, network_metadata, open_network_database
from runmap.paths.exploration import Snap
from runmap.paths.numeric_database import export_numeric_database, open_numeric_database
from runmap.routing.numeric_search import NumericEndpoint, load_numeric_graph, numeric_distances, numeric_shortest_path
from runmap.routing.numeric_store import SharedGraphStore, read_numeric_payload
from test_database_features import BOUNDS
from test_path_database import publish_fixture


SMALL = (375040, 6674990, 375060, 6675010)
EXPANDED = (374990, 6674990, 375210, 6675010)


class SharedGraphStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source, document, _ = publish_fixture(self.root)
        self.reference = self.root / "network.sqlite"
        self.numeric = self.root / "numeric.sqlite"
        with patch("runmap.paths.graph_chunks.MAX_CHUNK_SEGMENTS", 1):
            export_network_database(source, self.reference)
        export_numeric_database(self.reference, self.numeric)
        self.connection = open_numeric_database(self.numeric)
        self.addCleanup(self.connection.close)
        self.store = SharedGraphStore(self.connection, 1024**2)
        self.nodes = {key: number for number, key in self.connection.execute(
            "SELECT number, external_id FROM identities WHERE kind='node'")}
        node = document["nodes"][0]
        self.snap = Snap("sb", 0, 0, node["longitude"], node["latitude"], 0)

    def test_overlapping_windows_load_and_prepare_only_missing_chunks(self) -> None:
        with patch("runmap.routing.numeric_store.read_numeric_payload", wraps=read_numeric_payload) as reader, \
                patch("runmap.routing.numeric_store._routing_native.NumericChunk", wraps=_routing_native.NumericChunk) as prepare:
            small = load_numeric_graph(self.connection, SMALL, chunk_store=self.store)
            self.assertEqual(reader.call_count, 1)
            retained = next(iter(self.store.chunks.values()))
            expanded = load_numeric_graph(self.connection, EXPANDED, chunk_store=self.store)
            moved = load_numeric_graph(self.connection, (375050, 6674990, 375150, 6675010), chunk_store=self.store)
            self.assertEqual(reader.call_count, 2)
            self.assertEqual(prepare.call_count, 2)
            self.assertIn(retained, self.store.chunks.values())
        start, end = NumericEndpoint(node_id=self.nodes["s"]), NumericEndpoint(node_id=self.nodes["c"])
        self.assertEqual(numeric_distances(small, start).frontier_node_ids.tolist(), [self.nodes["b"]])
        actual = numeric_shortest_path(expanded, start, end)
        expected = numeric_shortest_path(load_numeric_graph(self.connection, EXPANDED), start, end)
        self.assertAlmostEqual(actual.cost, expected.cost)
        np.testing.assert_array_equal(actual.arc_ids, expected.arc_ids)
        self.assertFalse(numeric_distances(moved, start).frontier_node_ids.size)
        self.assertEqual(self.store.resident_bytes, sum(c.resident_bytes for c in self.store.chunks.values()))

    def test_eviction_and_connection_close_preserve_active_searches(self) -> None:
        graph = load_numeric_graph(self.connection, EXPANDED, chunk_store=self.store)
        start = NumericEndpoint(node_id=self.nodes["s"])
        expected = numeric_distances(graph, start)
        self.store.resize(0)
        self.assertFalse(self.store.chunks)
        self.assertEqual(self.store.resident_bytes, 0)
        self.connection.close()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: numeric_distances(graph, start), range(4)))
        for result in results:
            np.testing.assert_array_equal(result.node_ids, expected.node_ids)
            np.testing.assert_array_equal(result.values, expected.values)

    def test_lru_budget_zero_oversized_chunks_and_deadline_recovery(self) -> None:
        self.store.load(SMALL, None)
        size = self.store.resident_bytes
        self.store.resize(size)
        self.store.load(EXPANDED, None)
        self.assertLessEqual(self.store.resident_bytes, size)
        self.store.resize(1)
        with patch("runmap.routing.numeric_store.read_numeric_payload", wraps=read_numeric_payload) as reader:
            for _ in range(2):
                graph = load_numeric_graph(self.connection, SMALL, chunk_store=self.store)
                self.assertEqual(graph.native.segment_count, 1)
            self.assertEqual(reader.call_count, 2)
        self.assertFalse(self.store.chunks)
        self.store.resize(0)
        self.assertEqual(self.store.resident_bytes, 0)
        with self.assertRaises(ValueError):
            self.store.resize(-1)
        with self.assertRaises(TimeoutError):
            load_numeric_graph(self.connection, SMALL, chunk_store=self.store, deadline=monotonic()-1)
        with patch("runmap.routing.numeric_store._routing_native.NumericChunk", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                self.store.load(SMALL, monotonic()+5)
        self.assertFalse(self.store.chunks)
        self.store.resize(1024**2)
        self.assertEqual(load_numeric_graph(self.connection, SMALL, chunk_store=self.store).native.segment_count, 1)

    def test_generation_and_native_validation_are_preserved(self) -> None:
        with closing(open_numeric_database(self.numeric)) as other:
            with self.assertRaisesRegex(ValueError, "different artifact"):
                load_numeric_graph(other, SMALL, chunk_store=self.store)
        chunk = self.store.load(SMALL, None)[0]
        with self.assertRaises(ValueError):
            _routing_native.NumericGraph.from_chunks([chunk, chunk])
        with self.assertRaisesRegex(ValueError, "bounds"):
            _routing_native.NumericGraph.from_chunks([chunk], bounds=(1, 0, 0, 1))
        with self.assertRaises(TimeoutError):
            _routing_native.NumericGraph.from_chunks([chunk], timeout_seconds=0)
        with self.assertRaises(ValueError):
            _routing_native.NumericChunk(b"invalid")
        with self.assertRaises(TypeError):
            _routing_native.NumericChunk(bytearray(b"invalid"))
        with patch("runmap.routing.numeric_store.read_numeric_payload", wraps=read_numeric_payload) as reader:
            self.store.load(SMALL, None, verify_checksums=True)
            self.assertEqual(reader.call_count, 1)
            self.assertTrue(reader.call_args.kwargs["verify_checksums"])

    def test_api_preparation_coalesces_chunks_across_bounds_and_clears_on_shutdown(self) -> None:
        with closing(open_network_database(self.reference)) as reference:
            metadata = network_metadata(reference)
        data = NumericRoutingData(self.numeric, metadata)
        self.addCleanup(data.close)
        bounds = (SMALL, EXPANDED, BOUNDS, EXPANDED)
        with patch("runmap.routing.numeric_store.read_numeric_payload", wraps=read_numeric_payload) as reader, \
                ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda area: data.prepare_start(area, self.snap, monotonic()+5), bounds))
            self.assertEqual(reader.call_count, 3)
        self.assertLessEqual(data.resident_bytes + data.chunk_store.resident_bytes, data.cache_bytes)
        data.cache_bytes = 0
        data.prepare_start(SMALL, self.snap, monotonic()+5)
        self.assertFalse(data.graphs)
        self.assertFalse(data.chunk_store.chunks)
        data.close()
        start = NumericEndpoint(node_id=self.nodes["s"])
        self.assertEqual(len(numeric_distances(results[1][0], start).node_ids), 3)

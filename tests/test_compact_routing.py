"""Topology-only generations preserve costs while owning hill data exactly once."""

from contextlib import closing
import hashlib
import sqlite3
import struct
from time import monotonic
import unittest

import numpy as np

from runmap import _routing_native
from runmap.api.numeric_data import NumericRoutingData
from runmap.paths.compact_routing import compact_routing
from runmap.paths.numeric_database import open_numeric_database
from runmap.paths.numeric_format import decode_numeric_chunk
from runmap.paths.routing_inputs import export_routing_inputs, open_routing_inputs, read_input_payload
from runmap.paths.routing_inputs import COMPRESSED_HEADER, MAX_INPUT_BYTES
from runmap.routing.numeric_search import numeric_shortest_path
from runmap.routing.prepared_inputs import InputRequirements
import test_numeric_search
from test_database_features import BOUNDS


class CompactRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = test_numeric_search.NumericSearchTests("test_legacy_embedded_intervals_remain_usable")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.packed = self.fixture.numeric.with_name("routing-inputs-v1.sqlite")
        export_routing_inputs(self.fixture.numeric, self.packed)
        self.output = self.fixture.root / "compact"

    def test_compact_generation_preserves_partial_reverse_costs_after_close(self) -> None:
        source_hash = hashlib.sha256(self.fixture.numeric.read_bytes()).hexdigest()
        compact_routing(self.fixture.numeric, self.packed, self.output)
        numeric = self.output / "numeric-routing-v3.sqlite"
        with closing(open_numeric_database(numeric)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name='hill_intervals'").fetchone())
            for (payload,) in connection.execute("SELECT payload FROM numeric_chunks"):
                chunk = decode_numeric_chunk(payload)
                self.assertEqual(chunk.version, 3)
                self.assertFalse(any(name.startswith("hill_") for name in chunk.arrays))
                self.assertEqual(_routing_native.NumericChunk(payload).payload_bytes, len(payload))
            with closing(open_routing_inputs(self.output / "routing-inputs-v2.sqlite", connection)):
                pass
        self.assertEqual(hashlib.sha256(self.fixture.numeric.read_bytes()).hexdigest(), source_hash)
        with closing(NumericRoutingData(numeric, self.fixture.graph.metadata)) as data:
            graph, start, end = data.prepare(BOUNDS,
                (self.fixture._snap("sb", 25, 375025), self.fixture._snap("sb", 75, 375075)),
                monotonic()+5, requirements=InputRequirements(hills=True))
            data.cache_bytes = 0
            data.chunk_store.clear()
            assert data.input_store is not None
            data.input_store.clear()
        for a, b in ((start, end), (end, start)):
            actual = numeric_shortest_path(graph, a, b, hill_weight=6)
            expected = numeric_shortest_path(self.fixture.graph, a, b, hill_weight=6)
            self.assertEqual(actual.cost, expected.cost)
            np.testing.assert_array_equal(actual.hill_features, expected.hill_features)
            np.testing.assert_array_equal(actual.arc_ids, expected.arc_ids)

    def test_legacy_packed_cache_discards_embedded_hills(self) -> None:
        with self.assertRaises(TimeoutError):
            _routing_native.NumericChunk(b"invalid", external_hills=True, timeout_seconds=0)
        with closing(NumericRoutingData(self.fixture.numeric, self.fixture.graph.metadata)) as data:
            chunks = data.chunk_store.load(BOUNDS, None)
            with closing(open_numeric_database(self.fixture.numeric)) as connection:
                sizes = [len(payload) for (payload,) in connection.execute("SELECT payload FROM numeric_chunks ORDER BY number")]
            self.assertTrue(all(chunk.payload_bytes < size for chunk, size in zip(chunks, sizes)))

    def test_missing_stale_and_damaged_inputs_fail_without_publication(self) -> None:
        with closing(sqlite3.connect(self.packed)) as connection:
            connection.execute("UPDATE chunks SET sha256='damaged'")
            connection.commit()
        with self.assertRaisesRegex(ValueError, "checksum"):
            compact_routing(self.fixture.numeric, self.packed, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse([path for path in self.output.parent.glob("routing-*") if path.is_dir()])

    def test_topology_only_requires_paired_hill_identity(self) -> None:
        compact_routing(self.fixture.numeric, self.packed, self.output)
        numeric = self.output / "numeric-routing-v3.sqlite"
        packed = self.output / "routing-inputs-v2.sqlite"
        with closing(sqlite3.connect(packed)) as connection:
            connection.execute("UPDATE hill_details SET sha256='changed'")
            connection.commit()
        with self.assertRaisesRegex(ValueError, "hill source identity"):
            NumericRoutingData(numeric, self.fixture.graph.metadata)
        packed.unlink()
        with self.assertRaisesRegex(ValueError, "requires matching packed inputs"):
            NumericRoutingData(numeric, self.fixture.graph.metadata)
        with self.assertRaises(FileExistsError):
            compact_routing(self.fixture.numeric, self.packed, self.output)

    def test_attachment_validates_missing_duplicate_and_wrong_length_hills(self) -> None:
        with closing(open_numeric_database(self.fixture.numeric)) as numeric:
            payloads = [payload for (payload,) in numeric.execute("SELECT payload FROM numeric_chunks ORDER BY number")]
            numbers = [number for (number,) in numeric.execute("SELECT number FROM numeric_chunks ORDER BY number")]
        with closing(sqlite3.connect(self.packed)) as connection:
            chunks = [_routing_native.RoutingInputChunk(read_input_payload(connection, number, 1)) for number in numbers]
            raw = bytearray(read_input_payload(connection, numbers[0], 1))
        def attach(values: list[_routing_native.RoutingInputChunk]) -> None:
            graph = _routing_native.NumericGraph(payloads)
            graph.set_input_chunks(values)
        with self.assertRaisesRegex(ValueError, "lack a selected segment"):
            attach(chunks[:1])
        with self.assertRaisesRegex(ValueError, "[Dd]uplicate"):
            attach(chunks + chunks[:1])
        struct.pack_into("<d", raw, 32, 1234.0)
        chunks[0] = _routing_native.RoutingInputChunk(bytes(raw))
        with self.assertRaisesRegex(ValueError, "chainage"):
            attach(chunks)

    def test_compressed_records_reject_truncation_expansion_and_trailing_data(self) -> None:
        compact_routing(self.fixture.numeric, self.packed, self.output)
        with closing(sqlite3.connect(self.output / "routing-inputs-v2.sqlite")) as connection:
            number, original = connection.execute("SELECT number, payload FROM chunks LIMIT 1").fetchone()
            invalid = [original[:-1], original + b"trailing", original[:8],
                       COMPRESSED_HEADER.pack(b"RUNZIP01", MAX_INPUT_BYTES + 1) + original[16:]]
            for payload in invalid:
                connection.execute("UPDATE chunks SET payload=?, sha256=?, byte_size=? WHERE number=?",
                                   (payload, hashlib.sha256(payload).hexdigest(), len(payload), number))
                with self.assertRaises(ValueError):
                    read_input_payload(connection, number, 1)

    def test_conversion_rejects_missing_channel_chunk_and_cleans_staging(self) -> None:
        with closing(sqlite3.connect(self.packed)) as connection:
            connection.execute("DELETE FROM chunks WHERE number=(SELECT min(number) FROM chunks)")
            connection.commit()
        with self.assertRaisesRegex(ValueError, "missing"):
            compact_routing(self.fixture.numeric, self.packed, self.output)
        self.assertFalse(self.output.exists())

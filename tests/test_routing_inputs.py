"""Packed inputs retain source detail and reject stale or damaged artifacts."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

import numpy as np

from runmap import _routing_native
from runmap.paths.database import export_network_database, network_metadata
from runmap.paths.environment_join import _schema as environment_schema
from runmap.paths.numeric_database import export_numeric_database, open_numeric_database
from runmap.paths.numeric_format import NumericChunk, decode_numeric_chunk, encode_numeric_chunk
from runmap.paths.numeric_hills import read_hill_intervals
from runmap.paths.routing_inputs import (
    export_routing_inputs, input_channels, input_identity, main, open_routing_inputs,
    read_hill_payload, read_input_payload,
)
from runmap.routing.signals import SIGNAL_MODEL
from test_path_database import publish_fixture


class RoutingInputTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source, _, _ = publish_fixture(self.root)
        network = self.root / "network.sqlite"
        self.numeric_path = self.root / "numeric.sqlite"
        with patch("runmap.paths.graph_chunks.MAX_CHUNK_SEGMENTS", 1):
            export_network_database(source, network)
        export_numeric_database(network, self.numeric_path)
        self.numeric = open_numeric_database(self.numeric_path)
        self.addCleanup(self.numeric.close)
        self.output = self.root / "inputs.sqlite"
        self.environment_path = self.root / "environment.sqlite"
        self.signal_path = self.root / "signals.sqlite"
        self.segments = dict(self.numeric.execute(
            "SELECT external_id, number FROM identities WHERE kind='segment'"))
        self.nodes = dict(self.numeric.execute("SELECT external_id, number FROM identities WHERE kind='node'"))
        self._write_sources()
        self.environment = sqlite3.connect(self.environment_path)
        self.signals = sqlite3.connect(self.signal_path)
        self.addCleanup(self.environment.close)
        self.addCleanup(self.signals.close)

    def _write_sources(self) -> None:
        metadata = network_metadata(self.numeric)
        with closing(sqlite3.connect(self.environment_path)) as connection:
            environment_schema(connection)
            identity = dict(network_dataset_id=metadata.dataset_id,
                            network_feature_identity=metadata.feature_identity,
                            raster_dataset_id="test-raster", raster_sha256="raster-digest",
                            model="environment-cell-projection-v1")
            connection.executemany("INSERT INTO metadata VALUES (?, ?)",
                                   ((key, json.dumps(value)) for key, value in identity.items()))
            for name, number in self.segments.items():
                connection.executemany("INSERT INTO intervals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                    (number, name, 0, 0, 40, 39, 1, None, 0, .25, 1, .5),
                    (number, name, 1, 40, 100, 61, 0, .5, None, 0, 0, 1)])
            connection.commit()
        with closing(sqlite3.connect(self.signal_path)) as connection:
            connection.executescript("""
                PRAGMA user_version=2;
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE gates(segment INTEGER, offset_m REAL, unresolved INTEGER, directions INTEGER);
                CREATE TABLE turns(node INTEGER, incoming INTEGER, outgoing INTEGER, unresolved INTEGER);
            """)
            identity = dict(dataset_id=metadata.dataset_id, feature_identity=metadata.feature_identity,
                            model=SIGNAL_MODEL)
            connection.executemany("INSERT INTO metadata VALUES (?, ?)",
                                   ((key, json.dumps(value)) for key, value in identity.items()))
            connection.executemany("INSERT INTO gates VALUES (?, ?, ?, ?)", [
                (self.segments["sb"], 75, 1, 2), (self.segments["sb"], 25, 0, 1)])
            connection.execute("INSERT INTO turns VALUES (?, ?, ?, ?)",
                               (self.nodes["b"], self.segments["sb"], self.segments["bc"], 1))
            connection.commit()

    def _export(self, destination: Path | None = None) -> None:
        export_routing_inputs(self.numeric_path, destination or self.output,
                              environment_path=self.environment_path, signal_path=self.signal_path)

    def _chunk_id(self, connection: sqlite3.Connection, segment: str) -> int:
        return connection.execute("SELECT chunk_id FROM segment_chunks WHERE segment_id=?",
                                  (self.segments[segment],)).fetchone()[0]

    def test_deterministic_channels_and_exact_environment_signal_protocol(self) -> None:
        self._export()
        second = self.root / "second.sqlite"
        self._export(second)
        self.assertEqual(self.output.read_bytes(), second.read_bytes())
        with closing(open_routing_inputs(self.output, self.numeric, self.environment, self.signals)) as connection:
            self.assertEqual(input_channels(connection), (1, 2, 3))
            sb, bc = self._chunk_id(connection, "sb"), self._chunk_id(connection, "bc")
            self.assertNotEqual(sb, bc)
            self.assertEqual(connection.execute("SELECT count(*) FROM chunks").fetchone()[0], 9)
            payload = read_input_payload(connection, sb, 2)
            self.assertEqual(payload[:56], struct.pack("<8sIIQqdQQ", b"RUNINP01", 1, 2, 1,
                                                       self.segments["sb"], 100, 2, 0))
            first, second_row = struct.unpack_from("<8d", payload, 56), struct.unpack_from("<8d", payload, 120)
            self.assertEqual(first[:3], (0, 40, 1))
            self.assertTrue(math.isnan(first[3]))
            self.assertEqual(first[4:], (0, .25, 1, .5))
            self.assertEqual(second_row[:4], (40, 100, 0, .5))
            self.assertTrue(math.isnan(second_row[4]))
            self.assertEqual(len(payload), 184)
            # Both directional gates survive, ordered independently of insertion order.
            self.assertEqual(read_input_payload(connection, sb, 3),
                             struct.pack("<8sIIQqdQQ", b"RUNINP01", 1, 3, 1, self.segments["sb"], 100, 2, 0)
                             + struct.pack("<dQQdQQ", 25, 0, 1, 75, 1, 2))
            # An arrival belongs to its outgoing chunk even when the incoming link is elsewhere.
            self.assertEqual(read_input_payload(connection, bc, 3),
                             struct.pack("<8sIIQqdQQqqQ", b"RUNINP01", 1, 3, 1,
                                         self.segments["bc"], 100, 0, 1,
                                         self.nodes["b"], self.segments["sb"], 1))
            for number, channel in connection.execute("SELECT number, channel FROM chunks"):
                _routing_native.RoutingInputChunk(read_input_payload(connection, number, channel))

    def test_hill_totals_and_partial_reverse_intervals_are_preserved(self) -> None:
        self._export()
        with closing(open_routing_inputs(self.output, self.numeric, self.environment, self.signals)) as connection:
            for number, source in self.numeric.execute("SELECT number, payload FROM numeric_chunks"):
                chunk = decode_numeric_chunk(source)
                segment = int(chunk.arrays["segment_ids"][0])
                raw = read_hill_intervals(self.numeric, segment)
                assert raw is not None
                payload = read_input_payload(connection, number, 1)
                self.assertEqual(struct.unpack_from("<qdQQ", payload, 24), (segment, 100, 0, 0))
                self.assertEqual(payload[56:168], chunk.arrays["hill_totals"][0].astype("<f8").tobytes())
                self.assertEqual(len(payload), 168)
                self.assertEqual(read_hill_payload(connection, segment), raw)
                self.assertIsInstance(read_hill_payload(connection, segment), bytes)
                intervals = np.frombuffer(raw, dtype="<f8").reshape(-1, 16)
                self.assertEqual(intervals[0, 0], 0)
                self.assertAlmostEqual(intervals[-1, 1], 100)
                self.assertTrue(np.any(intervals[:, 2:9] != intervals[:, 9:16]))

    def test_legacy_inline_hills_match_separated_intervals(self) -> None:
        export_routing_inputs(self.numeric_path, self.output)
        with closing(sqlite3.connect(self.numeric_path)) as connection:
            for number, payload in list(connection.execute("SELECT number, payload FROM numeric_chunks")):
                chunk = decode_numeric_chunk(payload)
                raw = read_hill_intervals(connection, int(chunk.arrays["segment_ids"][0]))
                assert raw is not None
                values = np.frombuffer(raw, dtype="<f8").reshape(-1, 16)
                arrays = dict(chunk.arrays, hill_offsets=np.array([0, len(values)], dtype="<i8"),
                              hill_chainage=values[:, :2].copy(), hill_features=values[:, 2:].reshape(-1, 2, 7).copy())
                updated = encode_numeric_chunk(NumericChunk(replace(chunk.counts, intervals=len(values)), arrays))
                connection.execute("UPDATE numeric_chunks SET payload=?, sha256=?, byte_size=? WHERE number=?",
                                   (updated, hashlib.sha256(updated).hexdigest(), len(updated), number))
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        legacy = self.root / "legacy.sqlite"
        export_routing_inputs(self.numeric_path, legacy)
        with closing(sqlite3.connect(self.output)) as current, closing(sqlite3.connect(legacy)) as old:
            self.assertEqual(list(current.execute("SELECT number, channel, payload FROM chunks ORDER BY number")),
                             list(old.execute("SELECT number, channel, payload FROM chunks ORDER BY number")))
            self.assertEqual(list(current.execute("SELECT * FROM hill_details ORDER BY segment_id")),
                             list(old.execute("SELECT * FROM hill_details ORDER BY segment_id")))

    def test_sampled_hill_length_may_exceed_graph_chainage(self) -> None:
        with closing(sqlite3.connect(self.numeric_path)) as connection:
            for number, payload in list(connection.execute("SELECT number, payload FROM numeric_chunks")):
                chunk = decode_numeric_chunk(payload)
                segment = int(chunk.arrays["segment_ids"][0])
                raw = read_hill_intervals(connection, segment)
                assert raw is not None
                rows = np.frombuffer(raw, dtype="<f8").reshape(-1, 16).copy()
                rows[:, 2:] *= 2
                detail = zlib.compress(rows.tobytes())
                connection.execute("UPDATE hill_intervals SET payload=?, sha256=?, byte_size=? WHERE segment_id=?",
                                   (detail, hashlib.sha256(detail).hexdigest(), len(detail), segment))
                arrays = dict(chunk.arrays, hill_totals=chunk.arrays["hill_totals"] * 2)
                updated = encode_numeric_chunk(NumericChunk(chunk.counts, arrays))
                connection.execute("UPDATE numeric_chunks SET payload=?, sha256=?, byte_size=? WHERE number=?",
                                   (updated, hashlib.sha256(updated).hexdigest(), len(updated), number))
            connection.commit()
        export_routing_inputs(self.numeric_path, self.output)
        with closing(open_routing_inputs(self.output, self.numeric)) as connection:
            raw = read_hill_payload(connection, self.segments["bc"])
            rows = np.frombuffer(raw, dtype="<f8").reshape(-1, 16)
            self.assertTrue(np.all(rows[:, 8] > rows[:, 1] - rows[:, 0]))

    def test_corruption_and_missing_payload_are_always_rejected(self) -> None:
        self._export()
        with closing(sqlite3.connect(self.output)) as connection:
            number = self._chunk_id(connection, "sb")
            for assignment, message in (("byte_size=byte_size+1", "byte count"),
                                        ("sha256='incorrect'", "checksum"),
                                        ("payload=zeroblob(byte_size)", "checksum")):
                with self.subTest(assignment=assignment):
                    connection.execute(f"UPDATE chunks SET {assignment} WHERE number=? AND channel=1", (number,))
                    with self.assertRaisesRegex(ValueError, message):
                        read_input_payload(connection, number, 1)
                    connection.rollback()
            with self.assertRaisesRegex(ValueError, "missing"):
                read_input_payload(connection, number, 99)
            swapped = read_input_payload(connection, number, 2)
            connection.execute("UPDATE chunks SET payload=?, sha256=?, byte_size=? WHERE number=? AND channel=1",
                (swapped, hashlib.sha256(swapped).hexdigest(), len(swapped), number))
            with self.assertRaisesRegex(ValueError, "channel header"):
                read_input_payload(connection, number, 1)
            connection.rollback()
            segment = self.segments["sb"]
            for assignment, message in (("byte_size=byte_size+1", "byte count"),
                                        ("sha256='incorrect'", "checksum"),
                                        ("payload=zeroblob(byte_size)", "checksum"),
                                        ("payload=x'', byte_size=0", "byte count"),
                                        ("payload=zeroblob(127), byte_size=127", "byte count")):
                with self.subTest(detail=assignment):
                    connection.execute(f"UPDATE hill_details SET {assignment} WHERE segment_id=?", (segment,))
                    with self.assertRaisesRegex(ValueError, message):
                        read_hill_payload(connection, segment)
                    connection.rollback()
            with self.assertRaisesRegex(ValueError, "missing"):
                read_hill_payload(connection, -1)

    def test_identity_binds_numeric_chunks_metadata_and_optional_sources(self) -> None:
        self._export()
        original = input_identity(self.numeric, self.environment, self.signals)
        for connection, key in ((self.environment, "network_feature_identity"),
                                (self.environment, "model"), (self.environment, "raster_sha256"),
                                (self.signals, "feature_identity"), (self.signals, "model"),
                                (self.numeric, "feature_identity")):
            # The numeric reader is read-only, so change its source through a separate connection.
            writer = sqlite3.connect(self.numeric_path) if connection is self.numeric else connection
            try:
                previous = writer.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0]
                writer.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps("stale"), key))
                writer.commit()
                with self.assertRaises(ValueError):
                    open_routing_inputs(self.output, self.numeric, self.environment, self.signals)
                writer.execute("UPDATE metadata SET value=? WHERE key=?", (previous, key))
                writer.commit()
            finally:
                if writer is not connection:
                    writer.close()
        self.assertEqual(input_identity(self.numeric, self.environment, self.signals), original)
        with self.assertRaisesRegex(ValueError, "identity"):
            open_routing_inputs(self.output, self.numeric)
        with closing(sqlite3.connect(self.numeric_path)) as writer:
            writer.execute("UPDATE numeric_chunks SET sha256='stale' WHERE number=(SELECT min(number) FROM numeric_chunks)")
            writer.commit()
        with self.assertRaisesRegex(ValueError, "identity"):
            open_routing_inputs(self.output, self.numeric, self.environment, self.signals)

    def test_identity_binds_separated_hill_distribution_with_unchanged_totals(self) -> None:
        self._export()
        original = input_identity(self.numeric, self.environment, self.signals)
        chunks = list(self.numeric.execute("SELECT number, sha256 FROM numeric_chunks ORDER BY number"))
        metadata = list(self.numeric.execute("SELECT key, value FROM metadata ORDER BY key"))
        segment = self.segments["sb"]
        with closing(sqlite3.connect(self.numeric_path)) as writer:
            raw = read_hill_intervals(writer, segment)
            assert raw is not None
            rows = np.frombuffer(raw, dtype="<f8").reshape(-1, 16).copy()
            self.assertGreater(len(rows), 1)
            # Move a boundary while retaining the same features and segment totals.
            rows[0, 1] -= 1
            rows[1, 0] -= 1
            updated_raw = rows.tobytes()
            payload = zlib.compress(updated_raw)
            writer.execute("UPDATE hill_intervals SET payload=?, sha256=?, byte_size=? WHERE segment_id=?",
                           (payload, hashlib.sha256(payload).hexdigest(), len(payload), segment))
            writer.commit()
        self.assertEqual(list(self.numeric.execute("SELECT number, sha256 FROM numeric_chunks ORDER BY number")), chunks)
        self.assertEqual(list(self.numeric.execute("SELECT key, value FROM metadata ORDER BY key")), metadata)
        self.assertNotEqual(input_identity(self.numeric, self.environment, self.signals), original)
        with self.assertRaisesRegex(ValueError, "identity"):
            open_routing_inputs(self.output, self.numeric, self.environment, self.signals)
        refreshed = self.root / "refreshed.sqlite"
        self._export(refreshed)
        with closing(open_routing_inputs(refreshed, self.numeric, self.environment, self.signals)) as connection:
            self.assertEqual(read_hill_payload(connection, segment), updated_raw)

    def test_failed_exports_leave_no_destination_or_staging_files(self) -> None:
        before = set(self.root.iterdir())
        with patch("runmap.paths.routing_inputs._routing_native.RoutingInputChunk",
                   side_effect=ValueError("invalid packed input")):
            with self.assertRaisesRegex(ValueError, "invalid packed input"):
                self._export()
        self.assertEqual(set(self.root.iterdir()), before)
        self.environment.execute("DELETE FROM intervals")
        self.environment.commit()
        with self.assertRaisesRegex(ValueError, "lacks a selected segment"):
            self._export()
        self.assertEqual(set(self.root.iterdir()), before)

    def test_bad_source_identities_and_hill_coverage_prevent_publication(self) -> None:
        before = set(self.root.iterdir())
        previous = self.environment.execute("SELECT value FROM metadata WHERE key='raster_sha256'").fetchone()[0]
        self.environment.execute("DELETE FROM metadata WHERE key='raster_sha256'")
        self.environment.commit()
        with self.assertRaisesRegex(ValueError, "identity"):
            self._export()
        self.assertEqual(set(self.root.iterdir()), before)
        self.environment.execute("INSERT INTO metadata VALUES ('raster_sha256', ?)", (previous,))
        self.environment.commit()
        with closing(sqlite3.connect(self.numeric_path)) as connection:
            segment = self.segments["sb"]
            raw = read_hill_intervals(connection, segment)
            assert raw is not None
            rows = np.frombuffer(raw, dtype="<f8").reshape(-1, 16).copy()
            rows[-1, 1] -= 1
            detail = zlib.compress(rows.tobytes())
            connection.execute("UPDATE hill_intervals SET payload=?, sha256=?, byte_size=? WHERE segment_id=?",
                               (detail, hashlib.sha256(detail).hexdigest(), len(detail), segment))
            connection.commit()
        with self.assertRaises(ValueError):
            self._export()
        self.assertEqual(set(self.root.iterdir()), before)

    def test_atomic_publication_refuses_existing_and_racing_destinations(self) -> None:
        self.output.write_bytes(b"existing artifact")
        with self.assertRaises(FileExistsError):
            self._export()
        self.assertEqual(self.output.read_bytes(), b"existing artifact")
        race = self.root / "race.sqlite"
        before = set(self.root.iterdir())
        with patch("runmap.paths.routing_inputs.os.link", side_effect=FileExistsError("race")):
            with self.assertRaises(FileExistsError):
                self._export(race)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_reader_is_read_only_cross_thread_and_rejects_wrong_format(self) -> None:
        self._export()
        with closing(open_routing_inputs(self.output, self.numeric, self.environment, self.signals)) as connection:
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertEqual(executor.submit(input_channels, connection).result(), (1, 2, 3))
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM chunks")
        with closing(sqlite3.connect(self.output)) as writer:
            writer.execute("PRAGMA user_version=2")
        with self.assertRaisesRegex(ValueError, "format"):
            open_routing_inputs(self.output, self.numeric, self.environment, self.signals)

    def test_no_hill_model_exports_only_requested_channels_and_cli(self) -> None:
        flat = self.root / "flat"
        flat.mkdir()
        source, _, _ = publish_fixture(flat, terrain=False)
        network, numeric = flat / "network.sqlite", flat / "numeric.sqlite"
        export_network_database(source, network)
        export_numeric_database(network, numeric)
        empty, signals_only = flat / "empty.sqlite", flat / "signals-only.sqlite"
        export_routing_inputs(numeric, empty)
        with closing(open_numeric_database(numeric)) as connection:
            with closing(open_routing_inputs(empty, connection)) as inputs:
                self.assertEqual(input_channels(inputs), ())
                self.assertEqual(inputs.execute("SELECT count(*) FROM segment_chunks").fetchone()[0], 3)
                self.assertEqual(inputs.execute("SELECT count(*) FROM hill_details").fetchone()[0], 0)
        with patch("sys.argv", ["routing_inputs", str(self.numeric_path), str(signals_only),
                                "--environment", str(self.environment_path), "--signals", str(self.signal_path)]):
            main()
        with closing(open_routing_inputs(signals_only, self.numeric, self.environment, self.signals)) as inputs:
            self.assertEqual(input_channels(inputs), (1, 2, 3))


if __name__ == "__main__":
    unittest.main()

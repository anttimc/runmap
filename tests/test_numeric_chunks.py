"""Numeric routing contracts preserve global connections and reference features."""

from contextlib import closing, redirect_stdout
import heapq
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from runmap.cli import main
from runmap.paths.database import export_network_database, network_metadata, open_network_database
from runmap.paths.local_network import load_local_network
from runmap.paths.numeric_database import (StoredNumericChunk, export_numeric_database, iter_numeric_chunks,
                                           node_chunk_ids, open_numeric_database)
from runmap.paths.numeric_format import (HEADER, NumericChunk, decode_numeric_chunk,
                                         encode_numeric_chunk, validate_numeric_chunk)
from runmap.routing.search import shortest_distances
from test_database_features import BOUNDS
from test_path_database import publish_fixture


def _numeric_distances(chunks: list[StoredNumericChunk], start: int, profile: int) -> dict[int, float]:
    adjacency: dict[int, list[tuple[int, float]]] = {}
    for stored in chunks:
        data = stored.chunk.arrays
        for arc in np.flatnonzero(data["arc_allowed"][:, profile]):
            left, right = data["arc_nodes"][arc]
            source, target = int(data["node_ids"][left]), int(data["node_ids"][right])
            adjacency.setdefault(source, []).append((target, float(data["arc_lengths"][arc])))
    distances = {start: 0.0}
    queue = [(0.0, start)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        for target, length in adjacency.get(node, ()):
            proposed = distance + length
            if proposed < distances.get(target, float("inf")):
                distances[target] = proposed
                heapq.heappush(queue, (proposed, target))
    return distances


class NumericChunkTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, _, _ = publish_fixture(self.root)
        self.reference = self.root / "network.sqlite"
        self.numeric = self.root / "numeric.sqlite"
        with patch("runmap.paths.graph_chunks.MAX_CHUNK_SEGMENTS", 1):
            export_network_database(self.source, self.reference)
        export_numeric_database(self.reference, self.numeric)

    def test_columns_match_reference_topology_policies_geometry_and_hills(self) -> None:
        with closing(open_network_database(self.reference)) as reference, closing(open_numeric_database(self.numeric)) as numeric:
            metadata = network_metadata(reference)
            self.assertEqual(network_metadata(numeric), metadata)
            local = load_local_network(reference, BOUNDS)
            chunks = list(iter_numeric_chunks(numeric, BOUNDS, verify_checksums=True))
            self.assertEqual(len(chunks), 3)
            for stored in chunks:
                chunk = stored.chunk
                validate_numeric_chunk(chunk)
                data = chunk.arrays
                key = numeric.execute("SELECT external_id FROM identities WHERE kind='segment' AND number=?",
                                      (int(data["segment_ids"][0]),)).fetchone()[0]
                segment = local.routing.segments[key]
                self.assertEqual(float(data["arc_lengths"][0]), segment["length_m"])
                self.assertEqual(list(data["arc_reverse"]), [0, 1])
                self.assertEqual(list(data["outgoing_offsets"]), [0, 1, 2])
                for i, number in enumerate(data["node_ids"]):
                    node = numeric.execute("SELECT external_id FROM identities WHERE kind='node' AND number=?",
                                           (int(number),)).fetchone()[0]
                    self.assertEqual(data["node_degree"][i], local.routing.degree[node])
                segment_number = local.routing.integer.segment_indices[key]
                start, end = local.hills.interval_offsets[segment_number:segment_number + 2]
                np.testing.assert_allclose(data["hill_chainage"], local.hills.chainage[start:end])
                np.testing.assert_allclose(data["hill_features"], local.hills.features[start:end])
                np.testing.assert_allclose(data["hill_totals"][0], local.hills.totals[segment_number])
                points = local.routing.projected.segments
                lo, hi = points.offsets[segment_number:segment_number + 2]
                np.testing.assert_allclose(data["geometry_xy"], np.asarray(points.points)[lo:hi])
                for p, profile in enumerate(metadata.models.profiles):
                    expected = load_local_network(reference, BOUNDS, profile).routing.direction_cache[key]
                    self.assertEqual(bool(data["arc_allowed"][0, p]), True in expected)
                    self.assertEqual(bool(data["arc_allowed"][1, p]), False in expected)

    def test_cross_chunk_links_and_long_segment_selection(self) -> None:
        with closing(open_numeric_database(self.numeric)) as connection:
            chunks = list(iter_numeric_chunks(connection, (375040, 6674990, 375060, 6675010)))
            self.assertEqual(len(chunks), 1)
            data = chunks[0].chunk.arrays
            # Full crossing segment is retained despite both endpoints being outside.
            self.assertEqual(data["geometry_xy"].shape[0], 2)
            junction = connection.execute("SELECT number FROM identities WHERE kind='node' AND external_id='b'").fetchone()[0]
            self.assertEqual(len(node_chunk_ids(connection, junction)), 2)
            self.assertIn(chunks[0].number, node_chunk_ids(connection, junction))
            isolated = connection.execute("SELECT number FROM identities WHERE kind='node' AND external_id='s'").fetchone()[0]
            self.assertEqual(node_chunk_ids(connection, isolated), (chunks[0].number,))
            self.assertFalse(list(iter_numeric_chunks(connection, (0, 0, 1, 1))))

    def test_cross_chunk_distance_results_match_reference_for_each_profile(self) -> None:
        with closing(open_numeric_database(self.numeric)) as numeric, closing(open_network_database(self.reference)) as reference:
            chunks = list(iter_numeric_chunks(numeric, BOUNDS))
            identities = dict(numeric.execute("SELECT number, external_id FROM identities WHERE kind='node'"))
            start = next(number for number, key in identities.items() if key == "s")
            for position, profile in enumerate(network_metadata(numeric).models.profiles):
                expected = shortest_distances(load_local_network(reference, BOUNDS, profile).routing, "s")
                actual = {identities[key]: distance for key, distance in _numeric_distances(chunks, start, position).items()}
                self.assertEqual(actual, expected)

    def test_one_way_and_node_access_masks_survive_numeric_export(self) -> None:
        root = self.root / "restricted"
        root.mkdir()
        source, graph, _ = publish_fixture(root, terrain=False)
        graph["segments"][1]["tags"] = {"highway": "path", "oneway:foot": "-1"}
        graph["nodes"][-1]["foot"] = "no"
        source.write_text(json.dumps(graph))
        reference, numeric = root / "network.sqlite", root / "numeric.sqlite"
        export_network_database(source, reference)
        export_numeric_database(reference, numeric)
        with closing(open_numeric_database(numeric)) as connection:
            identities = dict(connection.execute("SELECT number, external_id FROM identities WHERE kind='segment'"))
            observed: dict[str, list[int]] = {}
            for stored in iter_numeric_chunks(connection, BOUNDS):
                data = stored.chunk.arrays
                for i, number in enumerate(data["segment_ids"]):
                    indices = np.flatnonzero(data["arc_segments"] == i)
                    observed[identities[int(number)]] = [int(value) for value in data["arc_allowed"][indices, 0]]
            self.assertEqual(observed["bc"], [0, 1])
            self.assertEqual(observed["de"], [0, 0])

    def test_loading_buffers_does_not_decode_records_and_buffers_outlive_connection(self) -> None:
        with closing(open_numeric_database(self.numeric)) as connection:
            with patch.object(json, "loads", side_effect=AssertionError("record decode")):
                chunks = list(iter_numeric_chunks(connection, BOUNDS))
        for stored in chunks:
            for values in stored.chunk.arrays.values():
                self.assertFalse(values.flags.writeable)
                if values.size:
                    with self.assertRaises(ValueError):
                        values.flat[0] = 0
            validate_numeric_chunk(stored.chunk)

    def test_invalid_headers_checksums_columns_and_indices_are_rejected(self) -> None:
        with closing(open_numeric_database(self.numeric)) as connection:
            chunk = next(iter_numeric_chunks(connection, BOUNDS)).chunk
        payload = encode_numeric_chunk(chunk)
        for broken in (b"", payload[:HEADER.size - 1], b"XXXXXXXX" + payload[8:], payload[:-1], payload + b"x"):
            with self.subTest(size=len(broken)), self.assertRaises(ValueError):
                decode_numeric_chunk(broken)
        with self.assertRaisesRegex(ValueError, "checksum"):
            decode_numeric_chunk(payload, sha256="incorrect")
        wrong_shape = dict(chunk.arrays, node_xy=chunk.arrays["node_xy"][:0])
        with self.assertRaisesRegex(ValueError, "column"):
            encode_numeric_chunk(NumericChunk(chunk.counts, wrong_shape))
        bad_nodes = chunk.arrays["arc_nodes"].copy()
        bad_nodes[0, 0] = chunk.counts.nodes
        with self.assertRaisesRegex(ValueError, "reference"):
            encode_numeric_chunk(NumericChunk(chunk.counts, dict(chunk.arrays, arc_nodes=bad_nodes)))
        bad_adjacency = chunk.arrays["outgoing_arcs"].copy()
        bad_adjacency[:] = 0
        with self.assertRaisesRegex(ValueError, "Adjacency"):
            encode_numeric_chunk(NumericChunk(chunk.counts, dict(chunk.arrays, outgoing_arcs=bad_adjacency)))
        with self.assertRaisesRegex(ValueError, "length"):
            altered = bytearray(payload)
            altered[16:24] = (2**63).to_bytes(8, "little")
            decode_numeric_chunk(bytes(altered))

    def test_export_does_not_overwrite_or_publish_failed_conversion(self) -> None:
        original = self.numeric.read_bytes()
        with self.assertRaises(FileExistsError):
            export_numeric_database(self.reference, self.numeric)
        self.assertEqual(self.numeric.read_bytes(), original)
        destination = self.root / "failed.sqlite"
        with patch("runmap.paths.numeric_database.prepare_numeric_chunk", side_effect=ValueError("bad features")):
            with self.assertRaisesRegex(ValueError, "bad features"):
                export_numeric_database(self.reference, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob("numeric-*.sqlite")))
        with closing(sqlite3.connect(self.numeric)) as connection, connection:
            connection.execute("UPDATE numeric_chunks SET payload = substr(payload, 1, length(payload) - 1)")
        with closing(open_numeric_database(self.numeric)) as connection:
            with self.assertRaisesRegex(ValueError, "byte count"):
                list(iter_numeric_chunks(connection, BOUNDS))

    def test_topology_only_export_cli_and_read_only_format(self) -> None:
        root = self.root / "topology"
        root.mkdir()
        source, _, _ = publish_fixture(root, terrain=False)
        reference, numeric = root / "network.sqlite", root / "numeric.sqlite"
        export_network_database(source, reference)
        with redirect_stdout(StringIO()) as output:
            main(["paths", "export-numeric", str(reference), str(numeric)])
        self.assertIn(str(numeric), output.getvalue())
        with closing(open_numeric_database(numeric)) as connection:
            for stored in iter_numeric_chunks(connection, BOUNDS):
                self.assertEqual(stored.chunk.counts.intervals, 0)
                self.assertEqual(stored.chunk.arrays["hill_features"].shape, (0, 2, 7))
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM numeric_chunks")
        with self.assertRaisesRegex(ValueError, "schema"):
            open_numeric_database(reference)

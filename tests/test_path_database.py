"""Local windows retain source data and distinguish unloaded connections."""

from contextlib import closing, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import cast
import unittest
from unittest.mock import patch

from rasterio.warp import transform

from runmap.cli import main
from runmap.paths.database import (count_network_segments, export_network_database, network_metadata,
                                   open_network_database, query_network)
from runmap.paths.measurements import graph_digest
from runmap.routing.types import GraphDocument, Measurement, MeasurementDocument
from test_measurements import enrich
from test_routing import graph_document


def fixture_graph() -> GraphDocument:
    longitude, latitude = transform("EPSG:3067", "EPSG:4326",
                                    [375000, 375100, 375200, 380000, 380100], [6675000] * 5)
    nodes = [(node, x, y) for node, x, y in zip(("s", "b", "c", "d", "e"), longitude, latitude)]
    return cast(GraphDocument, graph_document(nodes, [
        ("sb", "s", "b", 100, 0, {"highway": "path", "name": "Mäkipolku"}),
        ("bc", "b", "c", 100, 0, {"highway": "steps"}),
        ("de", "d", "e", 100, 0, {"highway": "path"}),
    ]))


def publish_fixture(root: Path, *, terrain: bool = True) -> tuple[Path, GraphDocument, dict[str, list[Measurement]]]:
    graph = fixture_graph()
    graph["summary"] = {}
    intervals: dict[str, list[Measurement]] = {}
    if terrain:
        enrich(graph, lambda i, record: (.1, 90))
        document = cast(MeasurementDocument, graph.pop("_measurements"))
        document["graph_digest"] = graph_digest(graph)
        intervals = document["segments"]
        intervals["sb"][0]["quality_flags"] = ["future_flag", "bridge", "bridge"]
        intervals["sb"][0]["grade_percent"] = None
        payload = json.dumps(document).encode()
        (root / "measurements.json").write_bytes(payload)
        graph["measurements"] = {"file": "measurements.json", "sha256": hashlib.sha256(payload).hexdigest()}
    path = root / "graph.json"
    path.write_text(json.dumps(graph))
    return path, graph, intervals


class PathDatabaseTests(unittest.TestCase):
    def test_bounded_segment_counts_preserve_envelope_selection(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root, terrain=False)
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                for bounds in ((0, 0, 1, 1), (375040, 6674990, 375060, 6675010),
                               (374990, 6674990, 375210, 6675010), (374000, 6674000, 381000, 6676000)):
                    expected = len(query_network(connection, bounds, include_measurements=False).graph["segments"])
                    for limit in (1, 2, 3, 4):
                        with self.subTest(bounds=bounds, limit=limit):
                            self.assertEqual(count_network_segments(connection, bounds, limit=limit), min(expected, limit))
                with self.assertRaisesRegex(ValueError, "positive"):
                    count_network_segments(connection, (0, 0, 1, 1), limit=0)
                with self.assertRaisesRegex(ValueError, "bounds"):
                    count_network_segments(connection, (1, 0, 0, 1), limit=1)

    def test_complete_crossing_segment_and_frontier_then_expansion(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, graph, intervals = publish_fixture(root)
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination)) as connection:
                metadata = network_metadata(connection)
                self.assertEqual(metadata.dataset_id, graph_digest(graph))
                self.assertEqual(metadata.method["manifest_id"], "test-manifest")
                self.assertEqual(metadata.measurement_sha256, graph["measurements"]["sha256"])
                # Neither endpoint is in this box, but the complete segment is returned.
                window = query_network(connection, (375040, 6674990, 375060, 6675010))
                self.assertEqual(window.graph["segments"], graph["segments"][:1])
                self.assertEqual(window.graph["arcs"], graph["arcs"][:2])
                self.assertEqual({node["id"] for node in window.graph["nodes"]}, {"s", "b"})
                self.assertEqual(window.measurements, {"sb": intervals["sb"]})
                self.assertEqual(window.frontier_nodes, ("b",))
                expanded = query_network(connection, (374990, 6674990, 375210, 6675010))
                self.assertEqual(expanded.graph["segments"], graph["segments"][:2])
                self.assertEqual(expanded.frontier_nodes, ())

    def test_empty_window_optional_measurements_and_read_only(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            destination = root / "network.sqlite"
            export_network_database(source, destination)
            with closing(open_network_database(destination, cache_kib=1024)) as connection:
                self.assertEqual(connection.execute("PRAGMA cache_size").fetchone()[0], -1024)
                window = query_network(connection, (0, 0, 1, 1))
                self.assertEqual(window.graph, {"nodes": [], "segments": [], "arcs": []})
                self.assertEqual(window.measurements, {})
                self.assertEqual(window.frontier_nodes, ())
                window = query_network(connection, (375040, 6674990, 375060, 6675010),
                                       include_measurements=False)
                self.assertEqual(len(window.graph["segments"]), 1)
                self.assertEqual(window.measurements, {})
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("DELETE FROM nodes")
                for bounds in ((1, 0, 0, 1), (0, 2, 1, 1), (0, 0, float("nan"), 1)):
                    with self.assertRaises(ValueError):
                        query_network(connection, bounds)

    def test_topology_only_cli_export_and_existing_destination(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root, terrain=False)
            destination = root / "network.sqlite"
            with redirect_stdout(StringIO()) as output:
                main(["paths", "export-sqlite", str(source), str(destination)])
            self.assertIn(str(destination), output.getvalue())
            with closing(open_network_database(destination)) as connection:
                self.assertIsNone(network_metadata(connection).method)
                self.assertEqual(query_network(connection, (374000, 6674000, 381000, 6676000)).measurements, {})
            before = destination.read_bytes()
            with self.assertRaises(FileExistsError):
                export_network_database(source, destination)
            self.assertEqual(before, destination.read_bytes())

    def test_bad_measurements_or_connectivity_do_not_publish(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root)
            (root / "measurements.json").write_text("corrupt")
            with self.assertRaisesRegex(ValueError, "checksum"):
                export_network_database(source, root / "network.sqlite")
            self.assertFalse((root / "network.sqlite").exists())
            source, graph, _ = publish_fixture(root, terrain=False)
            graph["segments"][0]["from"] = "missing"
            source.write_text(json.dumps(graph))
            with self.assertRaises(sqlite3.IntegrityError):
                export_network_database(source, root / "network.sqlite")
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["graph.json", "measurements.json"])

    def test_failed_publication_and_unsupported_schema(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root, terrain=False)
            destination = root / "network.sqlite"
            with patch("runmap.paths.database.os.link", side_effect=OSError("publication failed")):
                with self.assertRaisesRegex(OSError, "publication failed"):
                    export_network_database(source, destination)
            self.assertFalse(destination.exists())
            with closing(sqlite3.connect(destination)) as connection:
                connection.execute("PRAGMA user_version = 999")
            with self.assertRaisesRegex(ValueError, "schema"):
                open_network_database(destination)


if __name__ == "__main__":
    unittest.main()

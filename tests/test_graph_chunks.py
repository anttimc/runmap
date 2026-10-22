"""Spatial topology batches preserve graph identity across chunk boundaries."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runmap.paths.database import export_network_database, open_network_database, query_network
from runmap.paths.graph_chunks import MAX_CHUNK_SEGMENTS, query_graph_chunks
from test_path_database import publish_fixture
from test_routing import graph_document


class GraphChunkTests(unittest.TestCase):
    def test_cross_chunk_nodes_order_frontier_and_long_segment(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, graph, _ = publish_fixture(root, terrain=False)
            database = root / "network.sqlite"
            with patch("runmap.paths.graph_chunks.MAX_CHUNK_SEGMENTS", 1):
                export_network_database(source, database)
            with closing(open_network_database(database)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM graph_chunks").fetchone()[0], 3)
                local = query_graph_chunks(connection, (375040, 6674990, 375060, 6675010))
                self.assertEqual(local.graph["segments"], graph["segments"][:1])
                self.assertEqual(local.frontier_nodes, ("b",))
                full = query_graph_chunks(connection, (374000, 6674000, 381000, 6676000))
                self.assertEqual(full.graph["segments"], graph["segments"])
                self.assertEqual(full.graph["arcs"], graph["arcs"])
                self.assertEqual(len(full.graph["nodes"]), len(graph["nodes"]))
                self.assertEqual(full.frontier_nodes, ())

    def test_topology_query_does_not_read_individual_records_or_remote_chunks(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = publish_fixture(root, terrain=False)
            database = root / "network.sqlite"
            export_network_database(source, database)
            with closing(sqlite3.connect(database)) as connection, connection:
                # A remote chunk must never be decoded for a local request.
                connection.execute("UPDATE graph_chunks SET payload = 'invalid' WHERE number = (SELECT max(number) FROM graph_chunks)")
            with closing(open_network_database(database)) as connection:
                def authorize(action: int, table: str, column: str, database: str, trigger: str) -> int:
                    if action == sqlite3.SQLITE_READ and table in {"nodes", "arcs", "segments"}:
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK

                connection.set_authorizer(authorize)
                local = query_network(connection, (375040, 6674990, 375060, 6675010), include_measurements=False)
                self.assertEqual([s["id"] for s in local.graph["segments"]], ["sb"])
                self.assertEqual(local.frontier_nodes, ("b",))

    def test_dense_cell_has_bounded_chunks(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            count = MAX_CHUNK_SEGMENTS + 5
            nodes = [(str(i), 24.8 + i * .0000001, 60.2) for i in range(count + 1)]
            graph = graph_document(nodes, [(f"s{i}", str(i), str(i + 1), 1, 0, {"highway": "path"})
                                           for i in range(count)])
            source = root / "graph.json"
            source.write_text(json.dumps(graph))
            database = root / "network.sqlite"
            export_network_database(source, database)
            with closing(open_network_database(database)) as connection:
                sizes = [len(json.loads(row[0])["segments"])
                         for row in connection.execute("SELECT payload FROM graph_chunks")]
                self.assertEqual(sum(sizes), count)
                self.assertLessEqual(max(sizes), MAX_CHUNK_SEGMENTS)
                self.assertGreater(len(sizes), 1)

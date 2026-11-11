"""Bounded source-signal map delivery stays independent of routing attribution."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runmap.api.app import app
from runmap.api.sqlite_data import SQLitePathData
from runmap.paths.database import export_network_database, network_metadata, open_network_database
from runmap.paths.signal_points import SignalPoint, read_signal_points, write_signal_points
from runmap.routing.signals import SIGNAL_MODEL
from test_path_api import get
from test_path_database import publish_fixture


class SignalMapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source, _, _ = publish_fixture(root)
        network = root / "network.sqlite"
        export_network_database(source, network)
        with closing(open_network_database(network)) as connection:
            metadata = network_metadata(connection)
        with closing(sqlite3.connect(root / "signals.sqlite")) as connection:
            connection.execute("PRAGMA user_version=2")
            connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            connection.executemany("INSERT INTO metadata VALUES (?, ?)", ((k, json.dumps(v)) for k, v in {
                "dataset_id": metadata.dataset_id, "feature_identity": metadata.feature_identity,
                "model": SIGNAL_MODEL}.items()))
            write_signal_points(connection, (
                SignalPoint("1", 24.80, 60.20, {"highway": "crossing", "crossing:signals": "yes"}),
                SignalPoint("2", 24.81, 60.20, {"highway": "traffic_signals"}),
                SignalPoint("3", 24.82, 60.20, {"crossing:signals": "yes", "railway": "tram_crossing"}),
                SignalPoint("4", 25.0, 60.3, {"crossing": "traffic_signals"}),
            ))
            connection.commit()
        self.data = SQLitePathData(network)
        self.addCleanup(self.data.close)

    async def test_map_shows_source_nodes_even_when_not_attributed_to_routes(self) -> None:
        with patch.object(app.state, "paths", self.data, create=True):
            code, config = await get("/api/paths/signals/config")
            self.assertEqual(code, 200)
            self.assertEqual(config["dataset_id"], self.data.dataset_id)
            code, result = await get(f"/api/paths/signals?dataset={self.data.dataset_id}"
                                     "&west=24.79&south=60.19&east=24.83&north=60.21")
            self.assertEqual(code, 200)
            self.assertEqual(result["status"], "ready")
            self.assertEqual([f["id"] for f in result["features"]], ["1", "2", "3"])
            self.assertEqual([f["properties"]["kind"] for f in result["features"]],
                             ["pedestrian", "uncertain", "uncertain"])
            self.assertEqual(result["features"][0]["properties"]["tags"]["crossing:signals"], "yes")

    async def test_unavailable_stale_and_invalid_requests_are_explicit(self) -> None:
        with patch.object(app.state, "paths", None, create=True):
            self.assertEqual((await get("/api/paths/signals/config"))[0], 404)
        with patch.object(app.state, "paths", self.data, create=True):
            self.assertEqual((await get("/api/paths/signals?dataset=stale&west=24&south=60&east=25&north=61"))[0], 409)
            self.assertEqual((await get(f"/api/paths/signals?dataset={self.data.dataset_id}"
                                       "&west=25&south=60&east=24&north=61"))[0], 422)
            _, result = await get(f"/api/paths/signals?dataset={self.data.dataset_id}"
                                  "&west=20&south=55&east=30&north=70")
            self.assertEqual((result["status"], result["features"]), ("zoom_in", []))
            with patch("runmap.api.signals.MAX_FEATURES", 1):
                _, result = await get(f"/api/paths/signals?dataset={self.data.dataset_id}"
                                      "&west=24.79&south=60.19&east=24.83&north=60.21")
                self.assertEqual((result["status"], result["features"]), ("zoom_in", []))

    def test_exact_edges_are_refined_after_rtree_rounding(self) -> None:
        points = read_signal_points(self.data.signal_connection, (24.80, 60.20, 24.81, 60.21), 10)
        self.assertEqual([p["id"] for p in points], ["1", "2"])
        points = read_signal_points(self.data.signal_connection, (24.800001, 60.19, 24.809999, 60.21), 10)
        self.assertEqual(points, [])

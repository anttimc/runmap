"""Conditional input loading preserves partial-route costs and observations."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from typing import cast

from rasterio.warp import transform

from runmap import _routing_native
from runmap.api.runs import EndpointRequest, PreferencesRequest, RunRequest, SettingsRequest
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import RunAreaLimits, generate_database_run
from runmap.environment_fields import EnvironmentValues, EnvironmentSupport
from runmap.paths.database import network_metadata, open_network_database
from runmap.paths.environment_join import EnvironmentInterval, _schema, read_environment_segments
from runmap.routing.prepared_inputs import InputRequirements
from runmap.routing.signals import SIGNAL_MODEL
from runmap.routing.types import Segment
import test_numeric_runs
import test_sqlite_runs


class PreparedInputTests(unittest.TestCase):
    def setUp(self) -> None:
        test_sqlite_runs.SQLiteRunTests.setUp(self)
        original, source = test_numeric_runs.NumericRunTests.publish(self, test_sqlite_runs.detour_graph())
        original.close()
        self.database = source.with_name("network.sqlite")
        self.environment = source.with_name("environment.sqlite")
        self.bundle = source.parent / "environment"
        self.bundle.mkdir()
        self.bundle.joinpath("manifest.json").write_text(json.dumps({
            "dataset_id": "test-environment", "artifacts": [{"sha256": "test-raster"}]}))
        with closing(open_network_database(self.database)) as connection:
            metadata = network_metadata(connection)
            segments = [(number, cast(Segment, json.loads(payload))) for number, payload in connection.execute(
                "SELECT number, payload FROM segments")]
        self.numbers = {segment["id"]: number for number, segment in segments}
        self._write_environment(metadata.dataset_id, metadata.feature_identity, segments)
        self._write_signals(source.with_name("signals.sqlite"), metadata.dataset_id, metadata.feature_identity)
        self.data = SQLitePathData(self.database, environment_path=self.environment,
                                   environment_raster_bundle=self.bundle)
        self.addCleanup(self.data.close)
        longitude, latitude = transform("EPSG:3067", "EPSG:4326", [375000, 375000], [6675300, 6675900])
        self.request = RunRequest(dataset_id=self.data.dataset_id,
            home=EndpointRequest(longitude=longitude[0], latitude=latitude[0]),
            turnaround=EndpointRequest(longitude=longitude[1], latitude=latitude[1]),
            settings=SettingsRequest(avoidance_ratio=0))

    def _write_environment(self, dataset: str, features: str, segments: list[tuple[int, Segment]]) -> None:
        with closing(sqlite3.connect(self.environment)) as connection:
            _schema(connection)
            identity = dict(network_dataset_id=dataset, network_feature_identity=features,
                raster_dataset_id="test-environment", raster_sha256="test-raster", model="environment-cell-projection-v1")
            connection.executemany("INSERT INTO metadata VALUES (?, ?)",
                                   [(key, json.dumps(value)) for key, value in identity.items()])
            for number, segment in segments:
                length = float(segment["length_m"])
                connection.executemany("INSERT INTO intervals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                    (number, segment["id"], 0, 0, length/2, length/2, 1, None, 0, 0, 1, 1),
                    (number, segment["id"], 1, length/2, length, length/2, 1, 0, 0, 0, 0, 0)])
            connection.commit()

    def _write_signals(self, path: Path, dataset: str, features: str) -> None:
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript("""
                PRAGMA user_version=2;
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE gates(segment INTEGER, offset_m REAL, unresolved INTEGER, directions INTEGER);
                CREATE TABLE turns(node INTEGER, incoming INTEGER, outgoing INTEGER, unresolved INTEGER);
            """)
            identity = dict(dataset_id=dataset, feature_identity=features, model=SIGNAL_MODEL)
            connection.executemany("INSERT INTO metadata VALUES (?, ?)",
                                   [(key, json.dumps(value)) for key, value in identity.items()])
            connection.execute("INSERT INTO gates VALUES (?, 600, 0, 3)", (self.numbers["au"],))
            connection.commit()

    def test_off_reads_only_selected_route_and_matches_eager_diagnostics(self) -> None:
        for mode, reverse in (("one_way", False), ("one_way", True), ("round_trip", False)):
            request = self.request.model_copy(update={"mode": mode})
            if reverse:
                request = request.model_copy(update={"home": request.turnaround, "turnaround": request.home})
            with patch("runmap.api.sqlite_runs.search_input_requirements",
                       return_value=InputRequirements(environment=True, signals=True)):
                expected = generate_database_run(self.data, request)
            with patch("runmap.routing.numeric_search._attach_environment", side_effect=AssertionError("full-area environment")), \
                    patch("runmap.routing.numeric_search.read_signals", side_effect=AssertionError("unused signals")), \
                    patch("runmap.routing.prepared_inputs.read_environment_segments", wraps=read_environment_segments) as reader:
                actual = generate_database_run(self.data, request)
            self.assertEqual(actual.status, "complete")
            self.assertEqual(actual.model_dump(exclude={"elapsed_s"}), expected.model_dump(exclude={"elapsed_s"}))
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(set(reader.call_args.args[1]), {self.numbers["au"]})
            for leg in actual.candidates[0].legs:
                self.assertGreater(leg.observations.environment_unknown_water_m, 0)
                self.assertIsNone(leg.observations.signal_passages)

    def test_channel_changes_do_not_reuse_incompatible_graphs(self) -> None:
        assert self.data.numeric is not None
        for preferences in (PreferencesRequest(), PreferencesRequest(environment_greenery="low"),
                            PreferencesRequest(environment_greenery="high"), PreferencesRequest(signals="low"),
                            PreferencesRequest(signals="high"), PreferencesRequest(hills="high"), PreferencesRequest()):
            result = generate_database_run(self.data, self.request.model_copy(update={"preferences": preferences}))
            self.assertEqual(result.status, "complete")
            for leg in result.candidates[0].legs:
                if preferences.environment_greenery != "off":
                    self.assertGreater(leg.cost_breakdown.environment_greenery, 0)
                if preferences.signals != "off":
                    self.assertEqual(leg.observations.signal_passages, 1)
                    self.assertGreater(leg.cost_breakdown.signals, 0)
        states = {(key[1], key[2]) for key in self.data.numeric.graphs}
        self.assertEqual(states, {(False, False), (True, False), (False, True)})
        for key, (graph, _) in self.data.numeric.graphs.items():
            self.assertEqual(graph.native.environment_prepared, key[1])
            self.assertEqual(graph.native.signals_prepared, key[2])

    def test_unvisited_environment_is_deferred_but_missing_route_detail_fails(self) -> None:
        with closing(sqlite3.connect(self.environment)) as connection:
            connection.execute("DELETE FROM intervals WHERE segment_number != ?", (self.numbers["au"],))
            connection.commit()
        limits = RunAreaLimits(minimum_dimension_m=4000)
        self.assertEqual(generate_database_run(self.data, self.request, limits=limits).status, "complete")
        with self.assertRaisesRegex(ValueError, "lacks a selected segment"):
            generate_database_run(self.data, self.request.model_copy(update={
                "preferences": PreferencesRequest(environment_greenery="low")}), limits=limits)
        with closing(sqlite3.connect(self.environment)) as connection:
            connection.execute("DELETE FROM intervals")
            connection.commit()
        with self.assertRaisesRegex(ValueError, "lacks a selected segment"):
            generate_database_run(self.data, self.request)

    def test_native_route_diagnostics_validate_extents_spans_and_deadlines(self) -> None:
        rows = [(7, [EnvironmentInterval("s", 0, 20, 20, EnvironmentValues(1, None, 0, 0),
                                        EnvironmentSupport(1, 1))])]
        for lengths, spans in (([(7, 19)], [(7, 1, 10)]), ([(7, 20), (7, 20)], [(7, 1, 10)]),
                               ([(7, 20)], [(7, float("nan"), 10)]), ([(7, 20)], [(7, 1, 21)]),
                               ([], [(7, 1, 10)])):
            with self.assertRaises(ValueError):
                _routing_native.environment_unknown_lengths(rows, lengths, spans)
        with self.assertRaises(TimeoutError):
            _routing_native.environment_unknown_lengths(rows, [(7, 20)], [(7, 1, 10)], timeout_seconds=0)

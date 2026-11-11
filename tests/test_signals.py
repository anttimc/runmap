"""Signal preparation, state-dependent routes, endpoints, and artifact identity."""

from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from rasterio.warp import transform

from runmap.paths.database import export_network_database, network_metadata, open_network_database
from runmap.paths.graph import build_routing_graph, write_graph_outputs
from runmap.paths.local_network import load_local_network
from runmap.paths.numeric_database import export_numeric_database, open_numeric_database
from runmap.paths.signal_preparation import SignalSegment, prepare_signal_stages
from runmap.paths.signals import export_signals, open_signals, read_reference_signals, read_signals
from runmap.paths.exploration import Snap
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.graph import build_graph_index
from runmap.routing.numeric_planner import numeric_plan_run
from runmap.routing.numeric_search import NumericEndpoint, load_numeric_graph, numeric_shortest_path
from runmap.routing.run_types import RunSettings
from runmap.routing.signals import SignalData, SignalGate, SignalTraversal, signal_counts
from runmap.routing_reference.signal_search import signal_shortest_path
from runmap.routing.integer_graph import IntegerArc
from runmap.routing_reference.run_planner import plan_run
from test_routing import graph_document
from test_measurements import enrich
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import generate_database_run
from runmap.api.runs import RunRequest


BOUNDS = (374900, 6674900, 375400, 6675300)


class SignalSemanticsTests(unittest.TestCase):
    def test_boundary_splits_stops_reversal_and_repetition(self) -> None:
        data = SignalData({1: (SignalGate(1, 50),)}, {})
        def traversal(a: float, b: float) -> SignalTraversal:
            return SignalTraversal(1, a, b, 0)
        for a, b in ((0, 100), (100, 0)):
            self.assertEqual(signal_counts(data, None, traversal(a, b)), (1, 0))
            first, last = traversal(a, 50), traversal(50, b)
            self.assertEqual(signal_counts(data, None, first), (0, 0))
            self.assertEqual(signal_counts(data, first, last), (1, 0))
            self.assertEqual(signal_counts(data, first, traversal(50, a)), (0, 0))
            self.assertEqual(signal_counts(data, None, last), (0, 0))
        self.assertEqual(signal_counts(data, traversal(0, 100), traversal(100, 0)), (1, 0))
        self.assertEqual(signal_counts(data, None, traversal(50, 50)), (0, 0))

    def test_node_and_way_evidence_describe_one_stage_and_islands_stay_separate(self) -> None:
        segments = [SignalSegment(1, 1, 2, 10, "w1", {"highway": "footway", "footway": "crossing"}),
                    SignalSegment(2, 2, 3, 10, "w1", {"highway": "footway", "footway": "crossing"}),
                    SignalSegment(3, 3, 4, 5, "island", {"highway": "footway", "footway": "traffic_island"}),
                    SignalSegment(4, 4, 5, 10, "w2", {"highway": "footway", "crossing:signals": "yes"})]
        stages = prepare_signal_stages(segments, {2: {"crossing": "traffic_signals"}})
        self.assertEqual(len(stages), 2)
        self.assertEqual(sum(len(s.gates) for s in stages), 2)
        self.assertEqual(sum(len(s.turns) for s in stages), 0)

    def test_vehicle_controls_do_not_charge_parallel_road_and_tram_is_uncertain(self) -> None:
        roads = [SignalSegment(1, 1, 2, 10, "w1", {"highway": "residential"}),
                 SignalSegment(2, 2, 3, 10, "w1", {"highway": "residential"})]
        self.assertEqual(prepare_signal_stages(roads, {2: {"highway": "traffic_signals"}}), ())
        stages = prepare_signal_stages(roads, {2: {"crossing:signals": "yes", "railway": "tram_crossing"}})
        self.assertTrue(all(t.unresolved for t in stages[0].turns))

    def test_mixed_road_connections_at_a_known_crossing_are_not_free(self) -> None:
        links = [SignalSegment(1, 1, 2, 20, "path", {"highway": "cycleway"}),
                 SignalSegment(2, 2, 3, 20, "path", {"highway": "cycleway"}),
                 SignalSegment(3, 4, 2, 2, "road", {"highway": "residential"}),
                 SignalSegment(4, 2, 5, 10, "road", {"highway": "residential"})]
        stage = prepare_signal_stages(links, {2: {"crossing": "traffic_signals"}})[0]
        turns = {(t.incoming, t.outgoing): t for t in stage.turns}
        self.assertFalse(turns[1, 2].unresolved)
        self.assertTrue(turns[1, 3].unresolved)
        self.assertTrue(turns[4, 2].unresolved)
        self.assertNotIn((1, 1), turns)

    def test_crossing_way_does_not_hide_a_mixed_road_connection(self) -> None:
        links = [SignalSegment(1, 1, 2, 20, "crossing", {"highway": "cycleway", "cycleway": "crossing"}),
                 SignalSegment(2, 2, 3, 20, "crossing", {"highway": "cycleway", "cycleway": "crossing"}),
                 SignalSegment(3, 4, 2, 20, "road", {"highway": "residential"})]
        stages = prepare_signal_stages(links, {2: {"crossing:signals": "yes"}})
        self.assertEqual(sum(len(s.gates) for s in stages), 1)
        turns = [t for s in stages for t in s.turns]
        self.assertTrue(turns)
        self.assertTrue(all(t.unresolved for t in turns))
        self.assertTrue(all(3 in (t.incoming, t.outgoing) for t in turns))


class SignalRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        xs, ys = [375000, 375100, 375200, 375100, 375102], [6675000, 6675000, 6675000, 6675100, 6675000]
        lon, lat = transform("EPSG:3067", "EPSG:4326", xs, ys)
        self.coordinates = dict(zip(("s", "j", "t", "a", "u"), zip(lon, lat)))
        graph = graph_document([(key, *xy) for key, xy in self.coordinates.items()], [
            ("sj", "s", "j", 100, 0, {"highway": "path"}),
            ("jt", "j", "t", 100, 0, {"highway": "path"}),
            ("sa", "s", "a", 140, 0, {"highway": "path"}),
            ("aj", "a", "j", 100, 0, {"highway": "path"}),
            ("ju", "j", "u", 2, 0, {"highway": "residential"}),
        ])
        graph["summary"] = {}
        path = self.root / "graph.json"
        path.write_text(json.dumps(graph))
        self.network = self.root / "network.sqlite"
        self.numeric = self.root / "numeric.sqlite"
        with patch("runmap.paths.graph_chunks.MAX_CHUNK_SEGMENTS", 1):
            export_network_database(path, self.network)
        export_numeric_database(self.network, self.numeric)
        with closing(open_network_database(self.network)) as connection:
            self.local = load_local_network(connection, BOUNDS)
        with closing(open_numeric_database(self.numeric)) as connection:
            self.loaded = load_numeric_graph(connection, BOUNDS)
            self.segments = {key: number for number, key in connection.execute(
                "SELECT number, external_id FROM identities WHERE kind='segment'")}
            self.nodes = {key: number for number, key in connection.execute(
                "SELECT number, external_id FROM identities WHERE kind='node'")}

    def test_branching_requires_different_labels_for_different_arrivals(self) -> None:
        self.loaded.native.set_signals([], [(self.nodes["j"], self.segments["sj"], self.segments["jt"], False)])
        start, end = NumericEndpoint(node_id=self.nodes["s"]), NumericEndpoint(node_id=self.nodes["t"])
        off = numeric_shortest_path(self.loaded, start, end)
        low = numeric_shortest_path(self.loaded, start, end, signal_weight=60)
        high = numeric_shortest_path(self.loaded, start, end, signal_weight=180)
        self.assertEqual(off.cost, 200)
        self.assertEqual(low.cost, 260)
        self.assertEqual(high.cost, 340)
        self.assertEqual(high.segment_ids.tolist(), [self.segments[k] for k in ("sa", "aj", "jt")])
        self.assertEqual(high.signal_counts.sum(), 0)
        # A zero heuristic runs Dijkstra over the same independently retained movements.
        # Python's reference uses arc-state labels, unlike Rust's movement-state labels.
        from runmap.routing.signals import SignalTurn
        index = self.local.routing.integer
        turn = SignalTurn(index.node_indices["j"], index.segment_indices["sj"], index.segment_indices["jt"])
        signals = SignalData({}, {(turn.node, turn.incoming, turn.outgoing): turn})
        home, end_snap = self.snap("sj", 0), self.snap("jt", 100)
        result = plan_run(self.local.routing, home, end_snap, RunSettings(avoidance_ratio=0),
                          mode="one_way", preferences=PreferenceSettings(signals="high"), signals=signals)
        self.assertEqual(result.candidates[0].legs[0].path.cost, high.cost)
        self.assertEqual(result.candidates[0].legs[0].costs.signals, 0)
        def transition(previous: IntegerArc | None, current: IntegerArc) -> float:
            return 180.0 if (previous is not None and previous.segment == turn.incoming
                             and current.source == turn.node and current.segment == turn.outgoing) else 0.0
        oracle = signal_shortest_path(self.local.routing, "s", "t", arc_cost=lambda a: a.length_m,
            transition_cost=transition, heuristic=lambda a, b: 0.0)
        self.assertEqual(oracle.cost, high.cost)

    def test_directional_gate_and_turnaround_movement_context(self) -> None:
        segment = self.segments["sj"]
        self.loaded.native.set_signals([(segment, 50.0, False, 1)], [])
        a, b = NumericEndpoint(segment_id=segment, graph_offset_m=25), NumericEndpoint(segment_id=segment, graph_offset_m=75)
        self.assertEqual(numeric_shortest_path(self.loaded, a, b, signal_weight=60).cost, 110)
        self.assertEqual(numeric_shortest_path(self.loaded, b, a, signal_weight=60).cost, 50)
        # A leg ending exactly at the gate and a second continuing through it charge once.
        gate = NumericEndpoint(segment_id=segment, graph_offset_m=50)
        first = numeric_shortest_path(self.loaded, a, gate, signal_weight=60)
        last = numeric_shortest_path(self.loaded, gate, b, signal_weight=60,
            signal_arrival=(segment, float(first.start_m[-1]), float(first.end_m[-1])))
        self.assertEqual(first.cost + last.cost, 110)

    def test_leg_seam_keeps_arrival_at_a_crossing_node(self) -> None:
        self.loaded.native.set_signals([], [(self.nodes["j"], self.segments["sj"], self.segments["jt"], False)])
        first = numeric_shortest_path(self.loaded, NumericEndpoint(node_id=self.nodes["s"]),
                                      NumericEndpoint(node_id=self.nodes["j"]), signal_weight=60)
        last = numeric_shortest_path(self.loaded, NumericEndpoint(node_id=self.nodes["j"]),
            NumericEndpoint(node_id=self.nodes["t"]), signal_weight=60,
            signal_arrival=(int(first.segment_ids[-1]), float(first.start_m[-1]), float(first.end_m[-1])))
        self.assertEqual(first.cost, 100)
        self.assertEqual(last.cost, 160)

    def snap(self, segment: str, offset: float) -> Snap:
        record = self.local.routing.segments[segment]
        fraction = offset / record["length_m"]
        first, last = record["geometry"][0], record["geometry"][-1]
        lon, lat = (a + fraction * (b - a) for a, b in zip(first, last))
        from runmap.routing.geometry import projected_points
        import math
        points = projected_points(record["geometry"])
        chainage = math.dist(points[0], points[-1]) * fraction
        return Snap(segment_id=segment, graph_offset_m=offset, chainage_m=chainage,
                    longitude=lon, latitude=lat, offset_m=0)

    def test_partial_gate_counts_repeat_and_exact_turnaround_does_not_charge(self) -> None:
        segment = self.segments["sj"]
        self.loaded.native.set_signals([(segment, 50.0, False, 3)], [])
        index = self.local.routing.integer
        local_segment = index.segment_indices["sj"]
        signals = SignalData({local_segment: (SignalGate(local_segment, 50),)}, {})
        for a, b, expected in ((25, 75, 2), (25, 50, 0), (50, 75, 0), (75, 25, 2)):
            for mode in ("one_way", "round_trip"):
                with self.subTest(a=a, b=b, mode=mode):
                    count = expected if mode == "round_trip" else expected // 2
                    native = numeric_plan_run(self.loaded,
                        NumericEndpoint(segment_id=segment, graph_offset_m=a),
                        NumericEndpoint(segment_id=segment, graph_offset_m=b),
                        (375000 + a, 6675000), (375000 + b, 6675000), RunSettings(avoidance_ratio=0),
                        preferences=PreferenceSettings(signals="low"), mode=mode)
                    reference = plan_run(self.local.routing, self.snap("sj", a), self.snap("sj", b),
                        RunSettings(avoidance_ratio=0), preferences=PreferenceSettings(signals="low"),
                        signals=signals, mode=mode)
                    self.assertEqual(sum(p.signal_counts.sum() for p in native.candidates[0].legs), count)
                    self.assertAlmostEqual(sum(p.path.cost for p in reference.candidates[0].legs),
                                     sum(p.cost for p in native.candidates[0].legs))
                    self.assertEqual(sum(p.observations.signal_passages for p in reference.candidates[0].legs), count)

    def test_unavoidable_uncertain_gate_and_missing_features(self) -> None:
        segment = self.segments["jt"]
        a, b = NumericEndpoint(segment_id=segment, graph_offset_m=10), NumericEndpoint(segment_id=segment, graph_offset_m=90)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            numeric_shortest_path(self.loaded, a, b, signal_weight=60)
        self.loaded.native.set_signals([(segment, 50.0, True, 3)], [])
        result = numeric_shortest_path(self.loaded, a, b, signal_weight=180)
        self.assertEqual(result.cost, 260)
        self.assertEqual(result.signal_counts.tolist(), [[0, 1]])
        with self.assertRaisesRegex(ValueError, "already attached"):
            self.loaded.native.set_signals([], [])


class SignalArtifactTests(unittest.TestCase):
    def test_evidence_roundtrip_source_identity_bounded_loading_and_no_overwrite(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            raw = {"elements": [
                {"type": "node", "id": 1, "lon": 24.8, "lat": 60.2},
                {"type": "node", "id": 2, "lon": 24.801, "lat": 60.2,
                 "tags": {"highway": "crossing", "crossing:signals": "yes", "crossing:island": "no"}},
                {"type": "node", "id": 3, "lon": 24.802, "lat": 60.2},
                {"type": "way", "id": 4, "nodes": [1, 2, 3],
                 "tags": {"highway": "footway", "footway": "crossing", "crossing:signals": "yes"}},
            ]}
            source = root / "osm.json"
            source.write_text(json.dumps(raw))
            graph = build_routing_graph(raw, {"osm": {"sha256": hashlib.sha256(source.read_bytes()).hexdigest()}})
            expected = graph["nodes"][1]["tags"]
            graph_index = build_graph_index(graph)
            self.assertEqual(graph_index.nodes["2"]["tags"], expected)
            graph = deepcopy(graph)
            graph["experiment"] = {"bounds_epsg3067": [370000, 6670000, 385000, 6680000]}
            enrich(graph, lambda i, r: (0, 90))
            graph_path = write_graph_outputs(graph, root)["graph"]
            network, sidecar = root / "network.sqlite", root / "signals.sqlite"
            export_network_database(graph_path, network)
            export_signals(network, source, sidecar)
            with self.assertRaises(FileExistsError):
                export_signals(network, source, sidecar)
            with closing(open_network_database(network)) as connection:
                metadata = network_metadata(connection)
                with closing(open_signals(sidecar, metadata)) as signals:
                    numbers = [r[0] for r in connection.execute("SELECT number FROM segments")]
                    loaded = read_signals(signals, numbers)
                    self.assertEqual(sum(len(rows) for rows in loaded.gates.values()), 1)
                    self.assertEqual(read_signals(signals, []).gates, {})
                    self.assertEqual(signals.execute("SELECT count(*) FROM evidence WHERE kind='node'").fetchone()[0], 1)
                    local = load_local_network(connection, (370000, 6670000, 385000, 6680000))
                    reference = read_reference_signals(connection, signals, local.routing)
                    self.assertEqual(sum(len(rows) for rows in reference.gates.values()), 1)
                with self.assertRaisesRegex(ValueError, "does not match"):
                    open_signals(sidecar, replace(metadata, dataset_id="stale"))
            request = RunRequest(dataset_id=metadata.dataset_id, mode="one_way",
                home={"longitude": 24.8, "latitude": 60.2},
                turnaround={"longitude": 24.802, "latitude": 60.2},
                preferences={"signals": "low"})
            with closing(SQLitePathData(network)) as data:
                reference_response = generate_database_run(data, request)
            export_numeric_database(network, root / "numeric-routing-v2.sqlite")
            with closing(SQLitePathData(network)) as data:
                native_response = generate_database_run(data, request)
            for response in (reference_response, native_response):
                self.assertEqual(response.status, "complete")
                self.assertEqual(response.cost_model.capabilities.signals.supported_levels, ("off", "low", "high"))
                leg = response.candidates[0].legs[0]
                self.assertEqual(leg.observations.signal_passages, 1)
                self.assertEqual(leg.cost_breakdown.signals, 60)
                self.assertAlmostEqual(leg.cost, leg.distance_m + 60)
            self.assertAlmostEqual(reference_response.candidates[0].legs[0].cost,
                                   native_response.candidates[0].legs[0].cost)
            source.write_text("{}")
            with self.assertRaisesRegex(ValueError, "checksum"):
                export_signals(network, source, root / "wrong.sqlite")
            self.assertFalse((root / "wrong.sqlite").exists())

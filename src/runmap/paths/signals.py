"""Publish identity-bound signal sidecars and load bounded passage records."""

from collections import defaultdict
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from runmap.paths.database import NetworkMetadata, network_metadata, open_network_database
from runmap.paths.signal_preparation import SignalSegment, prepare_signal_stages
from runmap.paths.signal_points import SignalPoint, write_signal_points
from runmap.routing.deadlines import check_deadline
from runmap.routing.signals import SIGNAL_MODEL, SignalData, SignalGate, SignalTurn
from runmap.routing.types import Tags
from runmap.routing.graph import GraphData


SCHEMA_VERSION = 2


@dataclass(frozen=True)
class SignalSource:
    nodes: dict[str, Tags]
    ways: dict[str, Tags]
    digest: str
    points: tuple[SignalPoint, ...]


def _source_tags(connection: sqlite3.Connection, source: Path) -> SignalSource:
    recorded = json.loads(connection.execute(
        "SELECT value FROM metadata WHERE key='graph_metadata'").fetchone()[0])["source"]["osm"]
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != recorded.get("sha256"):
        raise ValueError("Signal source does not match the network's original OSM checksum")
    elements = json.loads(payload)["elements"]
    from runmap.paths.signal_preparation import signal_evidence

    points = tuple(SignalPoint(str(e["id"]), e["lon"], e["lat"], e.get("tags", {}))
                   for e in elements if e["type"] == "node" and (
                       signal_evidence(e.get("tags", {})) or e.get("tags", {}).get("highway") == "traffic_signals"))
    return SignalSource(
        {str(e["id"]): e.get("tags", {}) for e in elements if e["type"] == "node"},
        {str(e["id"]): e.get("tags", {}) for e in elements if e["type"] == "way"}, digest, points)


def _write_signals(network: Path, source: Path, destination: Path) -> None:
    with closing(open_network_database(network)) as original, closing(sqlite3.connect(destination)) as output:
        metadata = network_metadata(original)
        evidence = _source_tags(original, source)
        node_tags, way_tags, source_digest = evidence.nodes, evidence.ways, evidence.digest
        nodes = {identifier: number for number, identifier in original.execute("SELECT rowid, id FROM nodes")}
        if nodes.keys() - node_tags.keys():
            raise ValueError("Signal source lacks prepared nodes")
        tags = {number: node_tags[identifier] for identifier, number in nodes.items() if identifier in node_tags}
        segments: list[SignalSegment] = []
        for number, payload in original.execute("SELECT number, payload FROM segments ORDER BY number"):
            segment = json.loads(payload)
            way = segment["osm_way_id"]
            if way not in way_tags:
                raise ValueError("Signal source lacks a prepared way")
            segments.append(SignalSegment(number, nodes[segment["from"]], nodes[segment["to"]],
                segment["length_m"], way, way_tags[way]))
        stages = prepare_signal_stages(segments, tags)
        output.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE stages (id TEXT PRIMARY KEY, evidence TEXT NOT NULL);
            CREATE TABLE gates (stage TEXT NOT NULL REFERENCES stages(id), segment INTEGER NOT NULL,
                offset_m REAL NOT NULL, unresolved INTEGER NOT NULL, directions INTEGER NOT NULL,
                PRIMARY KEY(stage, segment, offset_m));
            CREATE INDEX gates_segment ON gates(segment);
            CREATE TABLE turns (stage TEXT NOT NULL REFERENCES stages(id), node INTEGER NOT NULL,
                incoming INTEGER NOT NULL, outgoing INTEGER NOT NULL, unresolved INTEGER NOT NULL,
                PRIMARY KEY(node, incoming, outgoing));
            CREATE INDEX turns_outgoing ON turns(outgoing);
            CREATE TABLE evidence (kind TEXT NOT NULL, id TEXT NOT NULL, tags TEXT NOT NULL,
                PRIMARY KEY(kind, id));
        """)
        output.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        write_signal_points(output, evidence.points)
        identity = {"dataset_id": metadata.dataset_id, "feature_identity": metadata.feature_identity,
                    "source_sha256": source_digest, "model": SIGNAL_MODEL,
                    "stage_count": len(stages)}
        output.executemany("INSERT INTO metadata VALUES (?, ?)",
                           ((key, json.dumps(value)) for key, value in identity.items()))
        for stage in stages:
            output.execute("INSERT INTO stages VALUES (?, ?)", (stage.identifier, stage.evidence))
            output.executemany("INSERT INTO gates VALUES (?, ?, ?, ?, ?)",
                ((stage.identifier, g.segment, g.offset_m, g.unresolved, g.directions) for g in stage.gates))
            output.executemany("INSERT INTO turns VALUES (?, ?, ?, ?, ?)",
                ((stage.identifier, t.node, t.incoming, t.outgoing, t.unresolved) for t in stage.turns))
        # Keep the source interpretation inspectable independently of runtime windows.
        relevant = lambda t: any(k.startswith(("crossing", "traffic_signals"))
                                  or k in {"railway", "footway", "cycleway"} for k in t)
        output.executemany("INSERT INTO evidence VALUES (?, ?, ?)",
            ((kind, identifier, json.dumps(value, sort_keys=True))
             for kind, values in (("node", node_tags), ("way", way_tags))
             for identifier, value in values.items() if relevant(value)))
        output.commit()


def export_signals(network: Path, source: Path, destination: Path) -> None:
    """Recover discarded evidence without changing the network or terrain generation."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, suffix=".sqlite", delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        _write_signals(network, source, staging)
        os.link(staging, destination)
    finally:
        staging.unlink(missing_ok=True)


def open_signals(path: Path, metadata: NetworkMetadata, *, check_same_thread: bool = True
                 ) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True,
                                 check_same_thread=check_same_thread)
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] not in (1, SCHEMA_VERSION):
            raise ValueError("Unsupported signal schema; re-export signals")
        identity = {key: json.loads(value) for key, value in connection.execute("SELECT * FROM metadata")}
        if (identity.get("dataset_id") != metadata.dataset_id
                or identity.get("feature_identity") != metadata.feature_identity
                or identity.get("model") != SIGNAL_MODEL):
            raise ValueError("Signal sidecar does not match the network or signal model")
        connection.execute("PRAGMA query_only=ON")
        return connection
    except BaseException:
        connection.close()
        raise


def read_signals(connection: sqlite3.Connection, segments: Sequence[int], *, deadline: float | None = None
                 ) -> SignalData:
    gates: dict[int, list[SignalGate]] = defaultdict(list)
    turns: dict[tuple[int, int, int], SignalTurn] = {}
    for start in range(0, len(segments), 500):
        check_deadline(deadline)
        batch = segments[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        for segment, offset, unresolved, directions in connection.execute(
                f"SELECT segment, offset_m, unresolved, directions FROM gates WHERE segment IN ({placeholders})", batch):
            if not math.isfinite(offset) or offset <= 0 or unresolved not in (0, 1) or directions not in (1, 2, 3):
                raise ValueError("Invalid prepared signal gate")
            gates[segment].append(SignalGate(segment, offset, bool(unresolved), directions))
        # Include arrivals outside the window: clipping cannot change passage attribution.
        for node, incoming, outgoing, unresolved in connection.execute(
                f"SELECT node, incoming, outgoing, unresolved FROM turns WHERE outgoing IN ({placeholders})", batch):
            if min(node, incoming, outgoing) < 0 or incoming == outgoing or unresolved not in (0, 1):
                raise ValueError("Invalid prepared signal movement")
            turns[node, incoming, outgoing] = SignalTurn(node, incoming, outgoing, bool(unresolved))
    check_deadline(deadline)
    return SignalData({key: tuple(value) for key, value in gates.items()}, turns)


def read_reference_signals(network: sqlite3.Connection, signals: sqlite3.Connection, graph: GraphData, *,
                           deadline: float | None = None) -> SignalData:
    """Translate persisted identities into one reference window's integer domains."""
    index = graph.integer
    if index is None:
        raise ValueError("Signal loading requires an integer graph")
    segments: dict[int, int] = {}
    nodes: dict[int, int] = {}
    for table, identifiers, mapping, column in (
        ("segments", index.segment_ids, segments, "number"), ("nodes", index.node_ids, nodes, "rowid"),
    ):
        for start in range(0, len(identifiers), 500):
            check_deadline(deadline)
            batch = identifiers[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            indices = index.segment_indices if table == "segments" else index.node_indices
            for number, key in network.execute(f"SELECT {column}, id FROM {table} WHERE id IN ({placeholders})", batch):
                mapping[number] = indices[key]
    stored = read_signals(signals, list(segments), deadline=deadline)
    for number, rows in stored.gates.items():
        key = index.segment_ids[segments[number]]
        if any(g.offset_m >= graph.segments[key]["length_m"] for g in rows):
            raise ValueError("Signal gate is outside physical segment chainage")
    gates = {segments[number]: tuple(SignalGate(segments[number], g.offset_m, g.unresolved, g.directions) for g in rows)
             for number, rows in stored.gates.items()}
    turns = {(nodes[t.node], segments[t.incoming], segments[t.outgoing]):
             SignalTurn(nodes[t.node], segments[t.incoming], segments[t.outgoing], t.unresolved)
             for t in stored.turns.values() if t.node in nodes and t.incoming in segments and t.outgoing in segments}
    return SignalData(gates, turns)

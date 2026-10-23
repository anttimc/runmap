"""Offline numeric routing artifacts, independent of the live reference backend."""

from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from typing import cast

from runmap.paths.database import NetworkMetadata, _validate_bounds, network_metadata, open_network_database
from runmap.paths.graph_chunks import TopologyChunk
from runmap.paths.numeric_format import FORMAT_VERSION, NumericChunk, decode_numeric_chunk, encode_numeric_chunk
from runmap.paths.numeric_preparation import prepare_numeric_chunk
from runmap.paths.numeric_hills import separate_hill_intervals
from runmap.routing.types import Bounds


NUMERIC_SCHEMA_VERSION = 2
PREPROCESSING_VERSION = "policy-union-v1"


@dataclass(frozen=True)
class NumericPayload:
    number: int
    byte_size: int
    payload: bytes


@dataclass(frozen=True)
class StoredNumericChunk:
    number: int
    byte_size: int
    chunk: NumericChunk
    payload: bytes


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE numeric_chunks (
            number INTEGER PRIMARY KEY, payload BLOB NOT NULL, sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK(byte_size >= 0)
        );
        CREATE VIRTUAL TABLE chunk_bounds USING rtree(number, west, east, south, north);
        CREATE TABLE node_chunks (
            node_id INTEGER NOT NULL, chunk_id INTEGER NOT NULL REFERENCES numeric_chunks(number),
            PRIMARY KEY(node_id, chunk_id)
        ) WITHOUT ROWID;
        CREATE TABLE identities (
            kind TEXT NOT NULL, number INTEGER NOT NULL, external_id TEXT NOT NULL,
            PRIMARY KEY(kind, number), UNIQUE(kind, external_id)
        ) WITHOUT ROWID;
        CREATE TABLE hill_intervals (
            segment_id INTEGER PRIMARY KEY, payload BLOB NOT NULL,
            sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL CHECK(byte_size > 0),
            raw_size INTEGER NOT NULL CHECK(raw_size > 0)
        );
    """)
    connection.execute(f"PRAGMA user_version = {NUMERIC_SCHEMA_VERSION}")


def _write_metadata(source: sqlite3.Connection, target: sqlite3.Connection) -> NetworkMetadata:
    metadata = network_metadata(source)
    target.executemany("INSERT INTO metadata VALUES (?, ?)", source.execute(
        "SELECT key, value FROM metadata WHERE key != 'graph_metadata'"))
    target.execute("INSERT INTO metadata VALUES ('numeric_format_version', ?)", (str(FORMAT_VERSION),))
    target.execute("INSERT INTO metadata VALUES ('numeric_preprocessing_version', ?)",
                   (json.dumps(PREPROCESSING_VERSION),))
    for kind, query in (
            ("node", "SELECT rowid, id FROM nodes"),
            ("segment", "SELECT number, id FROM segments"),
            ("arc", "SELECT number, id FROM arcs")):
        target.executemany("INSERT INTO identities VALUES (?, ?, ?)",
                           ((kind, number, key) for number, key in source.execute(query)))
    return metadata


def _prepare_routing_selection(source: sqlite3.Connection, metadata: NetworkMetadata) -> None:
    """Count incidence over the policy union so excluded links are not frontiers."""
    profiles = tuple(profile["id"] for profile in metadata.models.profiles)
    placeholders = ",".join("?" for _ in profiles)
    missing = source.execute(f"""
        SELECT s.id FROM segments s WHERE (
            SELECT count(*) FROM segment_policies p WHERE p.segment_id = s.id
              AND p.policy_id IN ({placeholders})
        ) != ? LIMIT 1
    """, (*profiles, len(profiles))).fetchone()
    if missing is not None:
        raise ValueError(f"Missing prepared routing policies for segment {missing[0]}")
    source.execute("CREATE TEMP TABLE numeric_segments (number INTEGER PRIMARY KEY)")
    source.execute(f"""
        INSERT INTO numeric_segments
        SELECT s.number FROM segments s WHERE EXISTS (
            SELECT 1 FROM segment_policies p WHERE p.segment_id = s.id
              AND p.policy_id IN ({placeholders}) AND (p.forward OR p.backward)
        )
    """, profiles)
    source.execute("""
        CREATE TEMP TABLE numeric_node_degree AS
        SELECT id, count(*) AS degree FROM (
            SELECT s.source AS id FROM segments s JOIN numeric_segments n ON n.number = s.number
            UNION ALL
            SELECT s.target FROM segments s JOIN numeric_segments n ON n.number = s.number
        ) GROUP BY id
    """)
    source.execute("CREATE UNIQUE INDEX numeric_degree_id ON numeric_node_degree(id)")


def _routing_chunk(source: sqlite3.Connection, chunk: TopologyChunk) -> TopologyChunk:
    numbers = [item["number"] for item in chunk["segments"]]
    placeholders = ",".join("?" for _ in numbers)
    retained = {row[0] for row in source.execute(
        f"SELECT number FROM numeric_segments WHERE number IN ({placeholders})", numbers)}
    segments = [item for item in chunk["segments"] if item["number"] in retained]
    keys = {item["record"]["id"] for item in segments}
    endpoints = {item["record"][end] for item in segments for end in ("from", "to")}
    placeholders = ",".join("?" for _ in endpoints)
    degrees = dict(source.execute(
        f"SELECT id, degree FROM numeric_node_degree WHERE id IN ({placeholders})", tuple(endpoints)))
    return TopologyChunk(segments=segments,
        arcs=[item for item in chunk["arcs"] if item["record"]["segment_id"] in keys],
        nodes=[dict(record=item["record"], degree=degrees[item["record"]["id"]])
               for item in chunk["nodes"] if item["record"]["id"] in endpoints])


def _write_chunks(source: sqlite3.Connection, target: sqlite3.Connection,
                  metadata: NetworkMetadata) -> None:
    _prepare_routing_selection(source, metadata)
    rows = source.execute("SELECT number, payload FROM graph_chunks ORDER BY number")
    for number, document in rows:
        records = _routing_chunk(source, cast(TopologyChunk, json.loads(document)))
        if not records["segments"]:
            continue
        chunk = prepare_numeric_chunk(source, records, metadata.models.profiles)
        chunk = separate_hill_intervals(target, chunk)
        payload = encode_numeric_chunk(chunk)
        target.execute("INSERT INTO numeric_chunks VALUES (?, ?, ?, ?)",
                       (number, payload, hashlib.sha256(payload).hexdigest(), len(payload)))
        bounds = (min(s["bounds"][0] for s in records["segments"]),
                  max(s["bounds"][2] for s in records["segments"]),
                  min(s["bounds"][1] for s in records["segments"]),
                  max(s["bounds"][3] for s in records["segments"]))
        target.execute("INSERT INTO chunk_bounds VALUES (?, ?, ?, ?, ?)", (number, *bounds))
        target.executemany("INSERT INTO node_chunks VALUES (?, ?)",
                           ((int(node), number) for node in chunk.arrays["node_ids"]))


def export_numeric_database(source_path: Path, destination: Path) -> None:
    """Publish atomically after bounded offline conversion and validation succeeds."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, prefix="numeric-", suffix=".sqlite", delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        with closing(open_network_database(source_path)) as source, closing(sqlite3.connect(staging)) as target:
            source.execute("BEGIN")
            _create_schema(target)
            with target:
                metadata = _write_metadata(source, target)
                _write_chunks(source, target, metadata)
        os.link(staging, destination)
    finally:
        staging.unlink(missing_ok=True)


def open_numeric_database(path: Path, cache_kib: int = 16384, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open metadata only; numeric chunk buffers are read on demand."""
    if cache_kib <= 0:
        raise ValueError("Page-cache target must be positive")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=check_same_thread)
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] not in (1, NUMERIC_SCHEMA_VERSION):
            raise ValueError("Unsupported numeric database schema")
        row = connection.execute("SELECT value FROM metadata WHERE key = 'numeric_format_version'").fetchone()
        if row is None or int(row[0]) != FORMAT_VERSION:
            raise ValueError("Unsupported numeric chunk format")
        network_metadata(connection)
        connection.execute(f"PRAGMA cache_size = {-cache_kib}")
        return connection
    except BaseException:
        connection.close()
        raise


def iter_numeric_payloads(connection: sqlite3.Connection, bounds: Bounds, *,
                          verify_checksums: bool = False) -> Iterator[NumericPayload]:
    """Supply immutable bytes directly to native traversal without NumPy wrappers."""
    _validate_bounds(bounds)
    west, south, east, north = bounds
    rows = connection.execute("""
        SELECT c.number, c.payload, c.sha256, c.byte_size
        FROM chunk_bounds b JOIN numeric_chunks c ON c.number = b.number
        WHERE b.west <= ? AND b.east >= ? AND b.south <= ? AND b.north >= ? ORDER BY c.number
    """, (east, west, north, south))
    for number, payload, checksum, byte_size in rows:
        if byte_size != len(payload):
            raise ValueError("Numeric chunk byte count mismatch")
        if verify_checksums and hashlib.sha256(payload).hexdigest() != checksum:
            raise ValueError("Numeric chunk checksum mismatch")
        yield NumericPayload(number, byte_size, payload)


def iter_numeric_chunks(connection: sqlite3.Connection, bounds: Bounds, *,
                        verify_checksums: bool = False) -> Iterator[StoredNumericChunk]:
    """Read bounded immutable array batches without decoding topology or features."""
    for stored in iter_numeric_payloads(connection, bounds, verify_checksums=verify_checksums):
        chunk = decode_numeric_chunk(stored.payload)
        yield StoredNumericChunk(stored.number, stored.byte_size, chunk, stored.payload)


def node_chunk_ids(connection: sqlite3.Connection, node_id: int) -> tuple[int, ...]:
    """Resolve cross-chunk connections by persisted numeric node identity."""
    return tuple(row[0] for row in connection.execute(
        "SELECT chunk_id FROM node_chunks WHERE node_id = ? ORDER BY chunk_id", (node_id,)))

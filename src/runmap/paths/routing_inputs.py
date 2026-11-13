"""Export identity-bound packed routing channels for direct native consumption."""

import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import zlib
from tempfile import NamedTemporaryFile

from runmap import _routing_native
from runmap.paths.database import network_metadata
from runmap.paths.environment_join import MODEL as ENVIRONMENT_MODEL, read_environment_segments
from runmap.paths.numeric_database import open_numeric_database, read_numeric_payload
from runmap.paths.numeric_format import NumericChunk, decode_numeric_chunk, validate_numeric_chunk
from runmap.paths.numeric_hills import read_hill_intervals
from runmap.paths.signals import open_signals, read_signals
from runmap.routing.signals import SIGNAL_MODEL


FORMAT_VERSION = 1
MAGIC = b"RUNINP01"
HILLS, ENVIRONMENT, SIGNALS = 1, 2, 3
HEADER = struct.Struct("<8sIIQ")
SEGMENT = struct.Struct("<qdQQ")
GATE = struct.Struct("<dQQ")
TURN = struct.Struct("<qqQ")
COMPRESSED_HEADER = struct.Struct("<8sQ")
MAX_INPUT_BYTES = 64 * 1024**2


def compress_payload(payload: bytes) -> bytes:
    """Bound independently compressed records so readers need no whole-area buffer."""
    if not payload or len(payload) > MAX_INPUT_BYTES:
        raise ValueError("Invalid routing input decompressed size")
    return COMPRESSED_HEADER.pack(b"RUNZIP01", len(payload)) + zlib.compress(payload, level=1)


def _decompress_payload(payload: bytes) -> bytes:
    if len(payload) < COMPRESSED_HEADER.size:
        raise ValueError("Truncated compressed routing input")
    magic, size = COMPRESSED_HEADER.unpack_from(payload)
    if magic != b"RUNZIP01" or not 0 < size <= MAX_INPUT_BYTES:
        raise ValueError("Invalid routing input decompressed size")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(payload[COMPRESSED_HEADER.size:], size + 1)
    except zlib.error as error:
        raise ValueError("Invalid compressed routing input") from error
    if len(raw) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("Invalid routing input decompressed size")
    return raw


def _metadata(connection: sqlite3.Connection) -> dict[str, object]:
    return {key: json.loads(value) for key, value in connection.execute(
        "SELECT key, value FROM metadata ORDER BY key")}


def _source_identities(numeric: sqlite3.Connection, environment: sqlite3.Connection | None,
                       signals: sqlite3.Connection | None) -> tuple[dict[str, object] | None,
                                                                  dict[str, object] | None]:
    metadata = network_metadata(numeric)
    env = None if environment is None else _metadata(environment)
    signal = None if signals is None else _metadata(signals)
    if environment is not None:
        if (environment.execute("PRAGMA user_version").fetchone()[0] != 1
                or env is None or env.get("network_dataset_id") != metadata.dataset_id
                or env.get("network_feature_identity") != metadata.feature_identity
                or env.get("model") != ENVIRONMENT_MODEL
                or any(not isinstance(env.get(key), str) or not env.get(key)
                       for key in ("raster_dataset_id", "raster_sha256"))):
            raise ValueError("Environment source or model identity mismatch")
    if signals is not None:
        if (signals.execute("PRAGMA user_version").fetchone()[0] not in (1, 2)
                or signal is None or signal.get("dataset_id") != metadata.dataset_id
                or signal.get("feature_identity") != metadata.feature_identity
                or signal.get("model") != SIGNAL_MODEL):
            raise ValueError("Signal source or model identity mismatch")
    return env, signal


def _hill_detail_identity(numeric: sqlite3.Connection) -> str:
    """Bind separated hill distributions with constant-memory metadata scanning."""
    digest = hashlib.sha256()
    for row in numeric.execute("SELECT segment_id, sha256, raw_size FROM hill_intervals ORDER BY segment_id"):
        digest.update(json.dumps(row, separators=(",", ":"), allow_nan=False).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def packed_hill_identity(connection: sqlite3.Connection) -> str:
    """Bind topology-only generations to the sole stored hill channel and detail."""
    digest = hashlib.sha256()
    for query in (
            "SELECT number, sha256, byte_size FROM chunks WHERE channel=1 ORDER BY number",
            "SELECT segment_id, sha256, byte_size FROM hill_details ORDER BY segment_id"):
        digest.update(query.encode())
        for row in connection.execute(query):
            digest.update(json.dumps(row, separators=(",", ":")).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def input_identity(numeric: sqlite3.Connection, environment: sqlite3.Connection | None = None,
                   signals: sqlite3.Connection | None = None) -> str:
    """Share the exporter fingerprint with runtime without reading chunk payloads."""
    env, signal = _source_identities(numeric, environment, signals)
    identity = {
        "numeric_schema": numeric.execute("PRAGMA user_version").fetchone()[0],
        "numeric_metadata": _metadata(numeric),
        "numeric_chunks": list(numeric.execute("SELECT number, sha256 FROM numeric_chunks ORDER BY number")),
        "environment": env, "signals": signal,
    }
    if identity["numeric_schema"] == 2:
        identity["numeric_hill_intervals"] = _hill_detail_identity(numeric)
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _channels(numeric: sqlite3.Connection, environment: sqlite3.Connection | None,
              signals: sqlite3.Connection | None) -> tuple[int, ...]:
    return tuple(channel for channel, available in (
        (HILLS, network_metadata(numeric).models.hill_model is not None),
        (ENVIRONMENT, environment is not None), (SIGNALS, signals is not None)) if available)


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        PRAGMA user_version=1;
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE chunks(
            number INTEGER NOT NULL, channel INTEGER NOT NULL CHECK(channel IN (1, 2, 3)),
            payload BLOB NOT NULL, sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL,
            PRIMARY KEY(number, channel)
        ) WITHOUT ROWID;
        CREATE TABLE segment_chunks(segment_id INTEGER PRIMARY KEY, chunk_id INTEGER NOT NULL);
        CREATE INDEX segment_chunk_ids ON segment_chunks(chunk_id);
        CREATE TABLE hill_details(
            segment_id INTEGER PRIMARY KEY, payload BLOB NOT NULL,
            sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL
        );
    """)


def _segment_lengths(chunk: NumericChunk) -> dict[int, float]:
    lengths: dict[int, float] = {}
    ids = chunk.arrays["segment_ids"]
    for position, value in zip(chunk.arrays["arc_segments"], chunk.arrays["arc_lengths"]):
        segment, length = int(ids[int(position)]), float(value)
        if segment in lengths and lengths[segment] != length:
            raise ValueError("Conflicting numeric segment lengths")
        lengths[segment] = length
    if set(lengths) != {int(segment) for segment in ids}:
        raise ValueError("Numeric segment lacks an arc length")
    return dict(sorted(lengths.items()))


def _hill_intervals(numeric: sqlite3.Connection, chunk: NumericChunk, position: int) -> bytes:
    """Preserve exact source rows for both inline and separated numeric schemas."""
    data = chunk.arrays
    intervals = read_hill_intervals(numeric, int(data["segment_ids"][position]))
    if intervals is None:
        lo, hi = (int(value) for value in data["hill_offsets"][position:position + 2])
        intervals = b"".join(struct.pack("<16d", *chainage, *features.reshape(-1))
                             for chainage, features in zip(data["hill_chainage"][lo:hi],
                                                           data["hill_features"][lo:hi]))
    if not intervals or len(intervals) % 128:
        raise ValueError("Invalid hill detail byte count")
    return intervals


def _hill_payload(chunk: NumericChunk, lengths: dict[int, float]) -> bytes:
    """Keep resident topology inputs small; endpoint detail is loaded separately."""
    data = chunk.arrays
    positions = {int(segment): position for position, segment in enumerate(data["segment_ids"])}
    payload = bytearray(HEADER.pack(MAGIC, FORMAT_VERSION, HILLS, len(lengths)))
    for segment, length in lengths.items():
        position = positions[segment]
        payload.extend(SEGMENT.pack(segment, length, 0, 0))
        payload.extend(struct.pack("<14d", *data["hill_totals"][position].reshape(-1)))
    return bytes(payload)


def _write_hill_details(target: sqlite3.Connection, numeric: sqlite3.Connection,
                       chunk: NumericChunk, lengths: dict[int, float]) -> None:
    """Validate coverage offline without retaining all-area interval objects."""
    for position, identifier in enumerate(chunk.arrays["segment_ids"]):
        segment = int(identifier)
        intervals = _hill_intervals(numeric, chunk, position)
        validation = (HEADER.pack(MAGIC, FORMAT_VERSION, HILLS, 1)
                      + SEGMENT.pack(segment, lengths[segment], len(intervals) // 128, 0)
                      + struct.pack("<14d", *chunk.arrays["hill_totals"][position].reshape(-1))
                      + intervals)
        _routing_native.RoutingInputChunk(validation)
        target.execute("INSERT INTO hill_details VALUES (?, ?, ?, ?)",
                       (segment, intervals, hashlib.sha256(intervals).hexdigest(), len(intervals)))


def _environment_payload(environment: sqlite3.Connection, lengths: dict[int, float]) -> bytes:
    intervals = read_environment_segments(environment, list(lengths))
    payload = bytearray(HEADER.pack(MAGIC, FORMAT_VERSION, ENVIRONMENT, len(lengths)))
    for segment, length in lengths.items():
        rows = intervals[segment]
        payload.extend(SEGMENT.pack(segment, length, len(rows), 0))
        for row in rows:
            values = (row.graph_start_m, row.graph_end_m, *row.values, *row.support)
            payload.extend(struct.pack("<8d", *(math.nan if value is None else value for value in values)))
    return bytes(payload)


def _signal_payload(signals: sqlite3.Connection, lengths: dict[int, float]) -> bytes:
    records = read_signals(signals, list(lengths))
    turns: dict[int, list[tuple[int, int, bool]]] = {}
    for turn in records.turns.values():
        turns.setdefault(turn.outgoing, []).append((turn.node, turn.incoming, turn.unresolved))
    payload = bytearray(HEADER.pack(MAGIC, FORMAT_VERSION, SIGNALS, len(lengths)))
    for segment, length in lengths.items():
        gates = sorted(records.gates.get(segment, ()), key=lambda g: (g.offset_m, g.unresolved, g.directions))
        arrivals = sorted(turns.get(segment, ()))
        payload.extend(SEGMENT.pack(segment, length, len(gates), len(arrivals)))
        for gate in gates:
            payload.extend(GATE.pack(gate.offset_m, gate.unresolved, gate.directions))
        for node, incoming, unresolved in arrivals:
            payload.extend(TURN.pack(node, incoming, unresolved))
    return bytes(payload)


def _write_chunks(target: sqlite3.Connection, numeric: sqlite3.Connection,
                  environment: sqlite3.Connection | None, signals: sqlite3.Connection | None,
                  channels: tuple[int, ...]) -> None:
    for (number,) in numeric.execute("SELECT number FROM numeric_chunks ORDER BY number"):
        stored = read_numeric_payload(numeric, number, verify_checksums=True)
        chunk = decode_numeric_chunk(stored.payload)
        validate_numeric_chunk(chunk)
        lengths = _segment_lengths(chunk)
        target.executemany("INSERT INTO segment_chunks VALUES (?, ?)",
                           ((segment, number) for segment in lengths))
        for channel in channels:
            if channel == HILLS:
                payload = _hill_payload(chunk, lengths)
                _write_hill_details(target, numeric, chunk, lengths)
            elif channel == ENVIRONMENT:
                assert environment is not None
                payload = _environment_payload(environment, lengths)
            else:
                assert signals is not None
                payload = _signal_payload(signals, lengths)
            _routing_native.RoutingInputChunk(payload)
            target.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                           (number, channel, payload, hashlib.sha256(payload).hexdigest(), len(payload)))


def export_routing_inputs(numeric_path: Path, destination: Path, *,
                          environment_path: Path | None = None, signal_path: Path | None = None) -> None:
    """Publish only a fully validated artifact, refusing existing destinations."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, prefix="routing-inputs-", suffix=".sqlite",
                            delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        with ExitStack() as stack:
            numeric = stack.enter_context(closing(open_numeric_database(numeric_path)))
            if numeric.execute("PRAGMA user_version").fetchone()[0] == 3:
                raise ValueError("Topology-only artifacts must retain their paired packed inputs")
            environment = None if environment_path is None else stack.enter_context(closing(
                sqlite3.connect(environment_path.resolve().as_uri() + "?mode=ro", uri=True)))
            signals = None if signal_path is None else stack.enter_context(closing(
                open_signals(signal_path, network_metadata(numeric))))
            for source in (numeric, environment, signals):
                if source is not None:
                    source.execute("BEGIN")
            identity = input_identity(numeric, environment, signals)
            channels = _channels(numeric, environment, signals)
            target = stack.enter_context(closing(sqlite3.connect(staging)))
            _schema(target)
            with target:
                target.executemany("INSERT INTO metadata VALUES (?, ?)",
                                   ((key, json.dumps(value)) for key, value in (
                                       ("format_version", FORMAT_VERSION), ("identity", identity),
                                       ("channels", channels))))
                _write_chunks(target, numeric, environment, signals, channels)
        os.link(staging, destination)
    finally:
        staging.unlink(missing_ok=True)


def open_routing_inputs(path: Path, numeric: sqlite3.Connection,
                        environment: sqlite3.Connection | None = None,
                        signals: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """Validate source identity while leaving packed payloads lazy and immutable."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    try:
        metadata = _metadata(connection)
        storage_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if storage_version not in (1, 2) or metadata.get("format_version") != storage_version:
            raise ValueError("Unsupported routing input format")
        if metadata.get("identity") != input_identity(numeric, environment, signals):
            raise ValueError("Routing input source identity mismatch")
        if (numeric.execute("PRAGMA user_version").fetchone()[0] == 3
                and _metadata(numeric).get("routing_hill_identity") != packed_hill_identity(connection)):
            raise ValueError("Routing hill source identity mismatch")
        channels = _channels(numeric, environment, signals)
        if metadata.get("channels") != list(channels) or input_channels(connection) != channels:
            raise ValueError("Invalid routing input channels")
        connection.execute("SELECT segment_id, chunk_id FROM segment_chunks LIMIT 0")
        connection.execute("SELECT segment_id, payload, sha256, byte_size FROM hill_details LIMIT 0")
        return connection
    except BaseException:
        connection.close()
        raise


def read_input_payload(connection: sqlite3.Connection, number: int, channel: int) -> bytes:
    """Always verify storage integrity before passing bytes to the native reader."""
    row = connection.execute("SELECT payload, sha256, byte_size FROM chunks WHERE number=? AND channel=?",
                             (number, channel)).fetchone()
    if row is None:
        raise ValueError("Routing input chunk is missing")
    payload, checksum, size = row
    if not isinstance(payload, bytes) or len(payload) != size:
        raise ValueError("Routing input byte count mismatch")
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise ValueError("Routing input checksum mismatch")
    if connection.execute("PRAGMA user_version").fetchone()[0] == 2:
        payload = _decompress_payload(payload)
    if len(payload) < HEADER.size or HEADER.unpack_from(payload)[:3] != (MAGIC, FORMAT_VERSION, channel):
        raise ValueError("Routing input channel header mismatch")
    return payload


def input_channels(connection: sqlite3.Connection) -> tuple[int, ...]:
    """Return available packed channels in protocol order."""
    return tuple(row[0] for row in connection.execute("SELECT DISTINCT channel FROM chunks ORDER BY channel"))


def read_hill_payload(connection: sqlite3.Connection, segment: int) -> bytes:
    """Fetch immutable endpoint detail without consulting the numeric source."""
    row = connection.execute("SELECT payload, sha256, byte_size FROM hill_details WHERE segment_id=?",
                             (segment,)).fetchone()
    if row is None:
        raise ValueError("Routing hill detail is missing")
    payload, checksum, size = row
    storage_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if (not isinstance(payload, bytes) or len(payload) != size or size <= 0
            or (storage_version == 1 and size % 128)):
        raise ValueError("Routing hill detail byte count mismatch")
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise ValueError("Routing hill detail checksum mismatch")
    if storage_version == 2:
        payload = _decompress_payload(payload)
    if len(payload) % 128:
        raise ValueError("Routing hill detail byte count mismatch")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("numeric", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--signals", type=Path)
    args = parser.parse_args()
    export_routing_inputs(args.numeric, args.output, environment_path=args.environment, signal_path=args.signals)


if __name__ == "__main__":
    main()

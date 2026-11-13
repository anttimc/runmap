"""Publish a topology-only numeric artifact paired with its existing packed inputs."""

import argparse
from contextlib import ExitStack, closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import mkdtemp

from runmap import _routing_native
from runmap.paths.numeric_database import open_numeric_database, read_numeric_payload
from runmap.paths.numeric_format import NumericChunk, decode_numeric_chunk, encode_numeric_chunk
from runmap.paths.routing_inputs import (
    _schema, compress_payload, input_channels, input_identity, open_routing_inputs, packed_hill_identity,
    read_hill_payload, read_input_payload,
)


def _compact_topology(source: sqlite3.Connection, target: sqlite3.Connection,
                      inputs: sqlite3.Connection) -> None:
    source.backup(target)
    channels = input_channels(inputs)
    with target:
        for (number,) in source.execute("SELECT number FROM numeric_chunks ORDER BY number"):
            stored = read_numeric_payload(source, number, verify_checksums=True)
            chunk = decode_numeric_chunk(stored.payload)
            arrays = {name: values for name, values in chunk.arrays.items() if not name.startswith("hill_")}
            payload = encode_numeric_chunk(NumericChunk(replace(chunk.counts, intervals=0), arrays, 3))
            native = _routing_native.NumericGraph([payload])
            native.set_input_chunks([_routing_native.RoutingInputChunk(read_input_payload(inputs, number, channel))
                                     for channel in channels])
            target.execute("UPDATE numeric_chunks SET payload=?, sha256=?, byte_size=? WHERE number=?",
                           (payload, hashlib.sha256(payload).hexdigest(), len(payload), number))
        target.execute("DROP TABLE IF EXISTS hill_intervals")
        target.execute("UPDATE metadata SET value='3' WHERE key='numeric_format_version'")
        target.execute("INSERT OR REPLACE INTO metadata VALUES ('routing_hill_identity', ?)",
                       (json.dumps(packed_hill_identity(inputs)),))
        target.execute("PRAGMA user_version=3")
    target.execute("VACUUM")


def _compact_inputs(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    _schema(target)
    target.execute("PRAGMA user_version=2")
    target.executemany("INSERT INTO metadata VALUES (?, ?)", source.execute("SELECT key, value FROM metadata"))
    target.execute("UPDATE metadata SET value='2' WHERE key='format_version'")
    target.executemany("INSERT INTO segment_chunks VALUES (?, ?)", source.execute("SELECT * FROM segment_chunks"))
    for number, channel in source.execute("SELECT number, channel FROM chunks ORDER BY number, channel"):
        raw = read_input_payload(source, number, channel)
        _routing_native.RoutingInputChunk(raw)
        payload = compress_payload(raw)
        target.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                       (number, channel, payload, hashlib.sha256(payload).hexdigest(), len(payload)))
    for (segment,) in source.execute("SELECT segment_id FROM hill_details ORDER BY segment_id"):
        payload = compress_payload(read_hill_payload(source, segment))
        target.execute("INSERT INTO hill_details VALUES (?, ?, ?, ?)",
                       (segment, payload, hashlib.sha256(payload).hexdigest(), len(payload)))
    target.commit()


def compact_routing(numeric_path: Path, input_path: Path, destination: Path, *,
                    environment_path: Path | None = None, signal_path: Path | None = None) -> None:
    """Atomically publish both files in a new directory; source files stay immutable."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix="routing-", dir=destination.parent))
    try:
        with ExitStack() as stack:
            numeric = stack.enter_context(closing(open_numeric_database(numeric_path)))
            environment = None if environment_path is None else stack.enter_context(closing(
                sqlite3.connect(environment_path.resolve().as_uri() + "?mode=ro", uri=True)))
            signals = None if signal_path is None else stack.enter_context(closing(
                sqlite3.connect(signal_path.resolve().as_uri() + "?mode=ro", uri=True)))
            # Establish consistent immutable source snapshots before validating their identity.
            for source in (numeric, environment, signals):
                if source is not None:
                    source.execute("BEGIN")
            inputs = stack.enter_context(closing(open_routing_inputs(input_path, numeric, environment, signals)))
            inputs.execute("BEGIN")
            packed = stack.enter_context(closing(sqlite3.connect(staging / "routing-inputs-v2.sqlite")))
            _compact_inputs(inputs, packed)
            topology = stack.enter_context(closing(sqlite3.connect(staging / "numeric-routing-v3.sqlite")))
            _compact_topology(numeric, topology, packed)
            with packed:
                packed.execute("UPDATE metadata SET value=? WHERE key='identity'",
                               (json.dumps(input_identity(topology, environment, signals)),))
            checked = stack.enter_context(closing(open_routing_inputs(
                staging / "routing-inputs-v2.sqlite", topology, environment, signals)))
            checked.execute("SELECT number FROM chunks LIMIT 1")
        # A hard link is insufficient for a two-file generation; publish the directory together.
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("numeric", type=Path)
    parser.add_argument("inputs", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--signals", type=Path)
    args = parser.parse_args()
    compact_routing(args.numeric, args.inputs, args.output_directory,
                    environment_path=args.environment, signal_path=args.signals)


if __name__ == "__main__":
    main()

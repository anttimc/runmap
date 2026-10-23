"""Keep partial-traversal hill detail outside shared routing chunks."""

from dataclasses import replace
import hashlib
import sqlite3
import zlib

import numpy as np

from runmap.paths.numeric_format import NumericChunk, validate_numeric_chunk


def separate_hill_intervals(connection: sqlite3.Connection, chunk: NumericChunk) -> NumericChunk:
    """Persist original interval values while retaining full-segment totals."""
    validate_numeric_chunk(chunk)
    data = chunk.arrays
    for position, segment in enumerate(data["segment_ids"]):
        lo, hi = (int(value) for value in data["hill_offsets"][position:position + 2])
        if lo == hi:
            continue
        values = np.column_stack((data["hill_chainage"][lo:hi], data["hill_features"][lo:hi].reshape(-1, 14)))
        raw = values.astype("<f8", copy=False).tobytes()
        payload = zlib.compress(raw, level=1)
        connection.execute("INSERT INTO hill_intervals VALUES (?, ?, ?, ?, ?)",
                           (int(segment), payload, hashlib.sha256(payload).hexdigest(), len(payload), len(raw)))
    arrays = dict(data, hill_offsets=np.zeros(chunk.counts.segments + 1, dtype="<i8"),
                  hill_chainage=np.empty((0, 2), dtype="<f8"), hill_features=np.empty((0, 2, 7), dtype="<f8"))
    return NumericChunk(replace(chunk.counts, intervals=0), arrays)


def read_hill_intervals(connection: sqlite3.Connection, segment_id: int) -> bytes | None:
    """Legacy artifacts carry intervals in their graph buffers instead."""
    if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
        return None
    row = connection.execute("SELECT payload, sha256, byte_size, raw_size FROM hill_intervals WHERE segment_id=?",
                             (segment_id,)).fetchone()
    if row is None:
        raise ValueError("Missing numeric hill intervals for endpoint segment")
    payload, checksum, size, raw_size = row
    if size != len(payload) or raw_size <= 0 or raw_size % 128:
        raise ValueError("Invalid numeric hill interval byte count")
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise ValueError("Numeric hill interval checksum mismatch")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(payload, raw_size + 1)
    except zlib.error as error:
        raise ValueError("Invalid compressed hill intervals") from error
    if len(raw) != raw_size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("Invalid decompressed hill interval size")
    return raw

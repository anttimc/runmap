"""Versioned, aligned numeric chunks for a graph-record-free routing runtime."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import struct
from typing import cast

import numpy as np
from numpy.typing import NDArray


NumericArray = NDArray[np.int64] | NDArray[np.float64] | NDArray[np.uint8]
FORMAT_VERSION = 2
TOPOLOGY_FORMAT_VERSION = 3
MAGIC = b"RUNNUM02"
HEADER = struct.Struct("<8sI4x6Q")
ALIGNMENT = 64


@dataclass(frozen=True)
class ChunkCounts:
    nodes: int
    segments: int
    arcs: int
    profiles: int
    points: int
    intervals: int


@dataclass(frozen=True)
class ArrayLayout:
    name: str
    dtype: str
    shape: tuple[int, ...]


@dataclass(frozen=True)
class NumericChunk:
    counts: ChunkCounts
    arrays: Mapping[str, NumericArray]
    version: int = FORMAT_VERSION


def array_layouts(counts: ChunkCounts, version: int = FORMAT_VERSION) -> tuple[ArrayLayout, ...]:
    n, s, a, p, g, h = (counts.nodes, counts.segments, counts.arcs,
                         counts.profiles, counts.points, counts.intervals)
    layouts = (
        ArrayLayout("node_ids", "<i8", (n,)),
        ArrayLayout("node_xy", "<f8", (n, 2)),
        ArrayLayout("node_degree", "<i8", (n,)),
        ArrayLayout("node_allowed", "u1", (n, p)),
        ArrayLayout("segment_ids", "<i8", (s,)),
        ArrayLayout("segment_nodes", "<i8", (s, 2)),
        ArrayLayout("segment_bounds", "<f8", (s, 4)),
        ArrayLayout("geometry_offsets", "<i8", (s + 1,)),
        ArrayLayout("geometry_xy", "<f8", (g, 2)),
        ArrayLayout("arc_ids", "<i8", (a,)),
        ArrayLayout("arc_nodes", "<i8", (a, 2)),
        ArrayLayout("arc_segments", "<i8", (a,)),
        ArrayLayout("arc_lengths", "<f8", (a,)),
        ArrayLayout("arc_reverse", "u1", (a,)),
        ArrayLayout("arc_allowed", "u1", (a, p)),
        ArrayLayout("outgoing_offsets", "<i8", (n + 1,)),
        ArrayLayout("outgoing_arcs", "<i8", (a,)),
        ArrayLayout("incoming_offsets", "<i8", (n + 1,)),
        ArrayLayout("incoming_arcs", "<i8", (a,)),
        ArrayLayout("hill_offsets", "<i8", (s + 1,)),
        ArrayLayout("hill_chainage", "<f8", (h, 2)),
        ArrayLayout("hill_features", "<f8", (h, 2, 7)),
        ArrayLayout("hill_totals", "<f8", (s, 2, 7)),
    )
    if version == TOPOLOGY_FORMAT_VERSION:
        if h:
            raise ValueError("Topology-only chunks cannot contain hill intervals")
        return layouts[:19]
    if version != FORMAT_VERSION:
        raise ValueError("Unsupported numeric chunk format")
    return layouts


def _aligned(position: int) -> int:
    return (position + ALIGNMENT - 1) // ALIGNMENT * ALIGNMENT


def _array_bytes(layout: ArrayLayout) -> int:
    count = 1
    for dimension in layout.shape:
        count *= dimension
    return count * np.dtype(layout.dtype).itemsize


def encode_numeric_chunk(chunk: NumericChunk) -> bytes:
    """Serialize only fixed-width arrays; graph records never enter this format."""
    c = chunk.counts
    magic = b"RUNNUM03" if chunk.version == TOPOLOGY_FORMAT_VERSION else MAGIC
    payload = bytearray(HEADER.pack(magic, chunk.version, c.nodes, c.segments,
                                    c.arcs, c.profiles, c.points, c.intervals))
    layouts = array_layouts(c, chunk.version)
    if set(chunk.arrays) != {layout.name for layout in layouts}:
        raise ValueError("Numeric chunk columns do not match the format")
    for layout in layouts:
        values = chunk.arrays[layout.name]
        if values.shape != layout.shape or values.dtype != np.dtype(layout.dtype):
            raise ValueError(f"Invalid numeric column {layout.name}")
        payload.extend(bytes(_aligned(len(payload)) - len(payload)))
        payload.extend(values.tobytes(order="C"))
    result = bytes(payload)
    validate_numeric_chunk(decode_numeric_chunk(result))
    return result


def decode_numeric_chunk(payload: bytes, *, sha256: str | None = None) -> NumericChunk:
    """Expose immutable buffer views without allocating individual graph records."""
    if len(payload) < HEADER.size:
        raise ValueError("Truncated numeric chunk header")
    magic, version, n, s, a, p, g, h = HEADER.unpack_from(payload)
    if (magic, version) not in ((MAGIC, FORMAT_VERSION), (b"RUNNUM03", TOPOLOGY_FORMAT_VERSION)):
        raise ValueError("Unsupported numeric chunk format")
    if sha256 is not None and hashlib.sha256(payload).hexdigest() != sha256:
        raise ValueError("Numeric chunk checksum mismatch")
    counts = ChunkCounts(n, s, a, p, g, h)
    layouts = array_layouts(counts, version)
    position = HEADER.size
    for layout in layouts:
        position = _aligned(position) + _array_bytes(layout)
    if position != len(payload):
        raise ValueError("Numeric chunk length does not match its column shapes")
    arrays: dict[str, NumericArray] = {}
    position = HEADER.size
    for layout in layouts:
        position = _aligned(position)
        size = _array_bytes(layout)
        values = np.frombuffer(payload, dtype=layout.dtype, count=size // np.dtype(layout.dtype).itemsize,
                               offset=position).reshape(layout.shape)
        arrays[layout.name] = cast(NumericArray, values)
        position += size
    return NumericChunk(counts, arrays, version)


def _validate_offsets(offsets: NumericArray, end: int) -> None:
    if offsets[0] != 0 or offsets[-1] != end or np.any(offsets[1:] < offsets[:-1]):
        raise ValueError("Invalid numeric chunk offsets")


def _validate_indices(values: NumericArray, upper: int) -> None:
    if np.any(values < 0) or np.any(values >= upper):
        raise ValueError("Numeric chunk reference is outside its column")


def validate_numeric_chunk(chunk: NumericChunk) -> None:
    """Check cross-column invariants offline, before publishing an artifact."""
    c, data = chunk.counts, chunk.arrays
    for name, count in (("node_ids", c.nodes), ("segment_ids", c.segments), ("arc_ids", c.arcs)):
        if len(np.unique(data[name])) != count or np.any(data[name] < 0):
            raise ValueError("Numeric chunk identifiers must be unique and nonnegative")
    for name in ("node_xy", "segment_bounds", "geometry_xy", "arc_lengths",
                 "hill_chainage", "hill_features", "hill_totals"):
        if name in data and not np.isfinite(data[name]).all():
            raise ValueError("Numeric chunk values must be finite")
    _validate_indices(data["segment_nodes"], c.nodes)
    _validate_indices(data["arc_nodes"], c.nodes)
    _validate_indices(data["arc_segments"], c.segments)
    _validate_offsets(data["geometry_offsets"], c.points)
    if chunk.version == FORMAT_VERSION:
        _validate_offsets(data["hill_offsets"], c.intervals)
    if (np.any(data["arc_lengths"] < 0) or np.any(data["arc_allowed"] > 1)
            or np.any(data["arc_reverse"] > 1) or np.any(data["node_allowed"] > 1)):
        raise ValueError("Invalid numeric arc lengths or policy flags")
    for direction, column in (("outgoing", 0), ("incoming", 1)):
        offsets, entries = data[direction + "_offsets"], data[direction + "_arcs"]
        _validate_offsets(offsets, c.arcs)
        if not np.array_equal(np.sort(entries), np.arange(c.arcs)):
            raise ValueError("Adjacency must include each chunk arc exactly once")
        nodes = np.repeat(np.arange(c.nodes), np.diff(offsets))
        if not np.array_equal(data["arc_nodes"][entries, column], nodes):
            raise ValueError("Adjacency does not match arc endpoints")
    incidence = np.bincount(data["segment_nodes"].reshape(-1), minlength=c.nodes)
    if np.any(data["node_degree"] < incidence):
        raise ValueError("Global node degree is smaller than the chunk incidence")
    bounds = data["segment_bounds"]
    if np.any(bounds[:, 0] > bounds[:, 2]) or np.any(bounds[:, 1] > bounds[:, 3]):
        raise ValueError("Numeric segment bounds must be ordered")

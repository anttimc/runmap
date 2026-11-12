"""Retain validated native topology chunks for one opened artifact generation."""

from collections import OrderedDict
import sqlite3
from time import monotonic

from runmap import _routing_native
from runmap.paths.numeric_database import numeric_chunk_ids, read_numeric_payload
from runmap.routing.deadlines import check_deadline
from runmap.routing.types import Bounds


class SharedGraphStore:
    """Coordinate chunk ownership under the numeric data owner's preparation lock."""

    def __init__(self, connection: sqlite3.Connection, cache_bytes: int) -> None:
        self.connection = connection
        self.chunks: OrderedDict[int, _routing_native.NumericChunk] = OrderedDict()
        self.resident_bytes = 0
        self.resize(cache_bytes)

    def resize(self, cache_bytes: int) -> None:
        if cache_bytes < 0:
            raise ValueError("Shared graph cache bytes must be nonnegative")
        self.cache_bytes = cache_bytes
        self._evict(0)

    def _evict(self, incoming_bytes: int) -> None:
        while self.chunks and self.resident_bytes + incoming_bytes > self.cache_bytes:
            _, removed = self.chunks.popitem(last=False)
            self.resident_bytes -= removed.resident_bytes

    def load(self, bounds: Bounds, deadline: float | None, *,
             verify_checksums: bool = False) -> list[_routing_native.NumericChunk]:
        chunks: list[_routing_native.NumericChunk] = []
        for number in numeric_chunk_ids(self.connection, bounds):
            check_deadline(deadline)
            chunk = self.chunks.get(number)
            if chunk is None or verify_checksums:
                if chunk is not None:
                    self.resident_bytes -= self.chunks.pop(number).resident_bytes
                stored = read_numeric_payload(self.connection, number, verify_checksums=verify_checksums)
                check_deadline(deadline)
                chunk = _routing_native.NumericChunk(
                    stored.payload, timeout_seconds=None if deadline is None else max(0.0, deadline - monotonic()))
                check_deadline(deadline)
                if chunk.resident_bytes <= self.cache_bytes:
                    self._evict(chunk.resident_bytes)
                    self.chunks[number] = chunk
                    self.resident_bytes += chunk.resident_bytes
            else:
                self.chunks.move_to_end(number)
            chunks.append(chunk)
        return chunks

    def clear(self) -> None:
        self.chunks.clear()
        self.resident_bytes = 0

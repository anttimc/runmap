"""Retain immutable native input chunks within one validated artifact generation."""

from collections import OrderedDict
import sqlite3
from time import monotonic

from runmap import _routing_native
from runmap.paths.numeric_database import numeric_chunk_ids
from runmap.paths.routing_inputs import input_channels, read_input_payload, read_hill_payload
from runmap.routing.deadlines import check_deadline
from runmap.routing.prepared_inputs import InputRequirements
from runmap.routing.types import Bounds


class SharedInputStore:
    """Use the dataset preparation lock to coalesce reads and native validation."""

    def __init__(self, numeric: sqlite3.Connection, connection: sqlite3.Connection, cache_bytes: int) -> None:
        self.numeric = numeric
        self.connection = connection
        self.channels = input_channels(connection)
        self.chunks: OrderedDict[tuple[int, int], _routing_native.RoutingInputChunk] = OrderedDict()
        self.hill_details: OrderedDict[int, bytes] = OrderedDict()
        self.resident_bytes = 0
        self.resize(cache_bytes)

    def resize(self, cache_bytes: int) -> None:
        if cache_bytes < 0:
            raise ValueError("Shared input cache bytes must be nonnegative")
        self.cache_bytes = cache_bytes
        self._evict(0)

    def _evict(self, incoming_bytes: int) -> None:
        while self.chunks and self.resident_bytes + incoming_bytes > self.cache_bytes:
            _, removed = self.chunks.popitem(last=False)
            self.resident_bytes -= removed.resident_bytes
        while self.hill_details and self.resident_bytes + incoming_bytes > self.cache_bytes:
            _, payload = self.hill_details.popitem(last=False)
            self.resident_bytes -= len(payload) + 128

    def partial_hills(self, segments: tuple[int, ...], deadline: float | None) -> dict[int, bytes]:
        result: dict[int, bytes] = {}
        for segment in dict.fromkeys(segments):
            check_deadline(deadline)
            payload = self.hill_details.get(segment)
            if payload is None:
                payload = read_hill_payload(self.connection, segment)
                check_deadline(deadline)
                size = len(payload) + 128
                if size <= self.cache_bytes:
                    self._evict(size)
                    self.hill_details[segment] = payload
                    self.resident_bytes += size
            else:
                self.hill_details.move_to_end(segment)
            result[segment] = payload
        return result

    def load(self, bounds: Bounds, requirements: InputRequirements,
             deadline: float | None) -> list[_routing_native.RoutingInputChunk]:
        requested = tuple(channel for channel, enabled in (
            (1, requirements.hills), (2, requirements.environment), (3, requirements.signals))
            if enabled and channel in self.channels)
        result: list[_routing_native.RoutingInputChunk] = []
        if not requested:
            return result
        for number in numeric_chunk_ids(self.numeric, bounds):
            for channel in requested:
                check_deadline(deadline)
                key = number, channel
                chunk = self.chunks.get(key)
                if chunk is None:
                    payload = read_input_payload(self.connection, number, channel)
                    chunk = _routing_native.RoutingInputChunk(payload,
                        timeout_seconds=None if deadline is None else max(0.0, deadline-monotonic()))
                    check_deadline(deadline)
                    if chunk.resident_bytes <= self.cache_bytes:
                        self._evict(chunk.resident_bytes)
                        self.chunks[key] = chunk
                        self.resident_bytes += chunk.resident_bytes
                else:
                    self.chunks.move_to_end(key)
                result.append(chunk)
        return result

    def clear(self) -> None:
        self.chunks.clear()
        self.hill_details.clear()
        self.resident_bytes = 0

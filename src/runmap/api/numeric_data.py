"""Manage numeric artifact access and reuse bounded native routing working sets."""

from collections import OrderedDict
from pathlib import Path
from threading import Lock
from time import monotonic
import sqlite3

from runmap.paths.database import NetworkMetadata, network_metadata
from runmap.paths.exploration import Snap
from runmap.paths.numeric_database import open_numeric_database
from runmap.paths.numeric_hills import read_hill_intervals
from runmap.routing.deadlines import check_deadline
from runmap.routing.geometry import projected_points
from runmap.routing.numeric_search import LoadedNumericGraph, NumericEndpoint, load_numeric_graph
from runmap.routing.types import Bounds


class NumericRoutingData:
    def __init__(self, path: Path, metadata: NetworkMetadata, cache_bytes: int = 128 * 1024**2) -> None:
        if cache_bytes < 0:
            raise ValueError("Numeric routing cache bytes must be nonnegative")
        self.connection = open_numeric_database(path, check_same_thread=False)
        try:
            if network_metadata(self.connection) != metadata:
                raise ValueError("Numeric routing artifact does not match the path database; re-export it")
        except BaseException:
            self.connection.close()
            raise
        self.lock = Lock()
        self.cache_bytes = cache_bytes
        self.resident_bytes = 0
        self.graphs: OrderedDict[Bounds, tuple[LoadedNumericGraph, int]] = OrderedDict()

    def prepare(self, bounds: Bounds, snaps: tuple[Snap, Snap], deadline: float, *, include_hill_intervals: bool = False
                ) -> tuple[LoadedNumericGraph, NumericEndpoint, NumericEndpoint]:
        graph, endpoints = self._prepare(bounds, snaps, deadline, include_hill_intervals=include_hill_intervals)
        return graph, endpoints[0], endpoints[1]

    def prepare_start(self, bounds: Bounds, snap: Snap, deadline: float
                      ) -> tuple[LoadedNumericGraph, NumericEndpoint]:
        graph, endpoints = self._prepare(bounds, (snap,), deadline)
        return graph, endpoints[0]

    def _prepare(self, bounds: Bounds, snaps: tuple[Snap, ...], deadline: float, *, include_hill_intervals: bool = False
                 ) -> tuple[LoadedNumericGraph, tuple[NumericEndpoint, ...]]:
        if not self.lock.acquire(timeout=max(0, deadline - monotonic())):
            raise TimeoutError("Numeric graph preparation deadline")
        try:
            check_deadline(deadline)
            endpoints = tuple(self._endpoint(snap, include_hill_intervals=include_hill_intervals) for snap in snaps)
            cached = self.graphs.get(bounds)
            if cached is not None:
                self.graphs.move_to_end(bounds)
                return cached[0], endpoints
            self.connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            try:
                graph = load_numeric_graph(self.connection, bounds, deadline=deadline)
            except sqlite3.OperationalError:
                check_deadline(deadline)
                raise
            finally:
                self.connection.set_progress_handler(None, 0)
            # Include an allowance for native CSR/index storage, beyond retained buffers.
            size = graph.buffer_bytes + graph.native.node_count * 256 + graph.native.segment_count * 256
            while self.graphs and self.resident_bytes + size > self.cache_bytes:
                _, (_, removed) = self.graphs.popitem(last=False)
                self.resident_bytes -= removed
            if size <= self.cache_bytes:
                self.graphs[bounds] = graph, size
                self.resident_bytes += size
            return graph, endpoints
        finally:
            self.lock.release()

    def _endpoint(self, snap: Snap, *, include_hill_intervals: bool = False) -> NumericEndpoint:
        row = self.connection.execute(
            "SELECT number FROM identities WHERE kind='segment' AND external_id=?", (snap.segment_id,)).fetchone()
        if row is None:
            raise ValueError("Snapped segment is missing from numeric artifact")
        return NumericEndpoint(segment_id=row[0], graph_offset_m=snap.graph_offset_m, chainage_m=snap.chainage_m,
                               point=projected_points([[snap.longitude, snap.latitude]])[0],
                               hill_intervals=read_hill_intervals(self.connection, row[0]) if include_hill_intervals else None)

    def close(self) -> None:
        with self.lock:
            self.graphs.clear()
            self.resident_bytes = 0
            self.connection.close()

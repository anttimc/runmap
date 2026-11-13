"""Manage numeric artifact access and reuse bounded native routing working sets."""

from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from threading import Lock
from time import monotonic
import sqlite3

from runmap.paths.database import NetworkMetadata, network_metadata
from runmap.paths.exploration import Snap
from runmap.paths.numeric_database import open_numeric_database
from runmap.paths.environment_join import open_environment_join
from runmap.paths.signals import open_signals
from runmap.paths.routing_inputs import open_routing_inputs
from runmap.routing.deadlines import check_deadline
from runmap.routing.geometry import projected_points
from runmap.routing.numeric_search import LoadedNumericGraph, NumericEndpoint, load_numeric_graph
from runmap.routing.numeric_store import SharedGraphStore
from runmap.routing.input_store import SharedInputStore
from runmap.routing.prepared_inputs import PreparedRoutingInputs, InputRequirements, endpoint_hill_detail
from runmap.routing.types import Bounds


class NumericRoutingData:
    def __init__(self, path: Path, metadata: NetworkMetadata, cache_bytes: int = 128 * 1024**2, *,
                 environment_path: Path | None = None, raster_bundle: Path | None = None,
                 signal_path: Path | None = None) -> None:
        if cache_bytes < 0:
            raise ValueError("Numeric routing cache bytes must be nonnegative")
        self.connection = open_numeric_database(path, check_same_thread=False)
        try:
            if network_metadata(self.connection) != metadata:
                raise ValueError("Numeric routing artifact does not match the path database; re-export it")
        except BaseException:
            self.connection.close()
            raise
        self.environment_connection: sqlite3.Connection | None = None
        self.signal_connection: sqlite3.Connection | None = None
        self.input_connection: sqlite3.Connection | None = None
        try:
            signal_path = signal_path or path.with_name("signals.sqlite")
            if signal_path.is_file():
                self.signal_connection = open_signals(signal_path, metadata, check_same_thread=False)
            if environment_path is not None:
                if raster_bundle is None:
                    raise ValueError("Environment routing requires a raster bundle")
                manifest = json.loads((raster_bundle / "manifest.json").read_text())
                self.environment_connection = open_environment_join(
                    environment_path, network_dataset_id=metadata.dataset_id,
                    network_feature_identity=metadata.feature_identity,
                    raster_dataset_id=manifest["dataset_id"],
                    raster_sha256=manifest["artifacts"][0]["sha256"])
            input_path = path.with_name("routing-inputs-v2.sqlite")
            if not input_path.is_file():
                input_path = path.with_name("routing-inputs-v1.sqlite")
            if input_path.is_file():
                self.input_connection = open_routing_inputs(input_path, self.connection,
                    self.environment_connection, self.signal_connection)
            if (self.connection.execute("PRAGMA user_version").fetchone()[0] == 3
                    and self.input_connection is None):
                raise ValueError("Topology-only routing requires matching packed inputs")
        except BaseException:
            if self.input_connection is not None:
                self.input_connection.close()
            if self.signal_connection is not None:
                self.signal_connection.close()
            if self.environment_connection is not None:
                self.environment_connection.close()
            self.connection.close()
            raise
        self.lock = Lock()
        self.cache_bytes = cache_bytes
        self.resident_bytes = 0
        self.graphs: OrderedDict[tuple[Bounds, bool, bool, bool], tuple[LoadedNumericGraph, int]] = OrderedDict()
        self.chunk_store = SharedGraphStore(self.connection, cache_bytes // 2,
                                            external_hills=self.input_connection is not None)
        self.input_store = (SharedInputStore(self.connection, self.input_connection, cache_bytes // 2)
                            if self.input_connection is not None else None)
        self.inputs = PreparedRoutingInputs(self.connection, self.environment_connection,
                                            self.signal_connection, self.input_connection)

    def prepare(self, bounds: Bounds, snaps: tuple[Snap, Snap], deadline: float, *,
                requirements: InputRequirements = InputRequirements()
                ) -> tuple[LoadedNumericGraph, NumericEndpoint, NumericEndpoint]:
        graph, endpoints = self._prepare(bounds, snaps, deadline, requirements=requirements)
        return graph, endpoints[0], endpoints[1]

    def prepare_start(self, bounds: Bounds, snap: Snap, deadline: float
                      ) -> tuple[LoadedNumericGraph, NumericEndpoint]:
        graph, endpoints = self._prepare(bounds, (snap,), deadline)
        return graph, endpoints[0]

    def _prepare(self, bounds: Bounds, snaps: tuple[Snap, ...], deadline: float, *,
                 requirements: InputRequirements = InputRequirements()
                 ) -> tuple[LoadedNumericGraph, tuple[NumericEndpoint, ...]]:
        if not self.lock.acquire(timeout=max(0, deadline - monotonic())):
            raise TimeoutError("Numeric graph preparation deadline")
        try:
            check_deadline(deadline)
            self.chunk_store.resize(self.cache_bytes // 2)
            if self.input_store is not None:
                self.input_store.resize(self.cache_bytes // 2)
            self._evict_graphs(0)
            endpoints = tuple(self._endpoint(snap) for snap in snaps)
            key = bounds, requirements.environment, requirements.signals, requirements.hills if self.input_store else False
            cached = self.graphs.get(key)
            if cached is not None:
                self.graphs.move_to_end(key)
                detail = self._partial_hills(endpoints, requirements, deadline)
                return replace(cached[0], hill_intervals=detail), endpoints
            graph = self._load_graph(bounds, requirements, deadline)
            hill_detail = self._partial_hills(endpoints, requirements, deadline)
            # Charge shared owners conservatively in both caches; active searches retain their own leases.
            size = graph.native.chunk_bytes + graph.native.index_bytes + graph.environment_bytes + graph.signal_bytes + graph.input_bytes
            self._evict_graphs(size)
            if size + self._shared_bytes() <= self.cache_bytes:
                self.graphs[key] = graph, size
                self.resident_bytes += size
            return replace(graph, hill_intervals=hill_detail), endpoints
        finally:
            self._evict_graphs(0)
            self.lock.release()

    def _load_graph(self, bounds: Bounds, requirements: InputRequirements, deadline: float) -> LoadedNumericGraph:
        legacy = self.input_store is None
        self.connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
        try:
            graph = load_numeric_graph(self.connection, bounds, deadline=deadline,
                environment_connection=self.inputs.environment if requirements.environment and legacy else None,
                signal_connection=self.inputs.signals if requirements.signals and legacy else None,
                chunk_store=self.chunk_store)
            if self.input_store is not None:
                chunks = self.input_store.load(bounds, requirements, deadline)
                if chunks:
                    graph.native.set_input_chunks(chunks, timeout_seconds=max(0.0, deadline-monotonic()))
                    graph = replace(graph, input_bytes=graph.native.input_bytes)
            return graph
        except sqlite3.OperationalError:
            check_deadline(deadline)
            raise
        finally:
            self.connection.set_progress_handler(None, 0)

    def _evict_graphs(self, incoming_bytes: int) -> None:
        graph_budget = self.cache_bytes - self._shared_bytes()
        while self.graphs and self.resident_bytes + incoming_bytes > graph_budget:
            _, (_, removed) = self.graphs.popitem(last=False)
            self.resident_bytes -= removed

    def _shared_bytes(self) -> int:
        return self.chunk_store.resident_bytes + (self.input_store.resident_bytes if self.input_store else 0)

    def _partial_hills(self, endpoints: tuple[NumericEndpoint, ...],
                       requirements: InputRequirements, deadline: float) -> dict[int, bytes]:
        if not requirements.hills:
            return {}
        segments = tuple(endpoint.segment_id for endpoint in endpoints if endpoint.segment_id is not None)
        if self.input_store is not None:
            return self.input_store.partial_hills(segments, deadline)
        return {endpoint.segment_id: payload for endpoint in endpoints
                if endpoint.segment_id is not None
                and (payload := endpoint_hill_detail(self.inputs, endpoint.segment_id)) is not None}

    @contextmanager
    def input_access(self, deadline: float | None) -> Iterator[PreparedRoutingInputs]:
        acquired = self.lock.acquire() if deadline is None else self.lock.acquire(timeout=max(0, deadline-monotonic()))
        if not acquired:
            raise TimeoutError("Prepared routing inputs deadline")
        try:
            check_deadline(deadline)
            yield self.inputs
        finally:
            self.lock.release()

    def _endpoint(self, snap: Snap) -> NumericEndpoint:
        row = self.connection.execute(
            "SELECT number FROM identities WHERE kind='segment' AND external_id=?", (snap.segment_id,)).fetchone()
        if row is None:
            raise ValueError("Snapped segment is missing from numeric artifact")
        return NumericEndpoint(segment_id=row[0], graph_offset_m=snap.graph_offset_m, chainage_m=snap.chainage_m,
                               point=projected_points([[snap.longitude, snap.latitude]])[0])

    def close(self) -> None:
        with self.lock:
            self.graphs.clear()
            self.resident_bytes = 0
            self.chunk_store.clear()
            if self.input_store is not None:
                self.input_store.clear()
            if self.input_connection is not None:
                self.input_connection.close()
            self.connection.close()
            if self.environment_connection is not None:
                self.environment_connection.close()
            if self.signal_connection is not None:
                self.signal_connection.close()

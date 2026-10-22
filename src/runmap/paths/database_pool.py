"""Bound read-only connections and lease each exclusively to one API request."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, LifoQueue
import sqlite3
from threading import Lock

from runmap.paths.database import network_metadata, open_network_database


class DatabaseBusyError(RuntimeError):
    pass


class DatabasePool:
    def __init__(self, path: Path, capacity: int = 2, cache_kib: int = 16384, timeout_s: float = 5) -> None:
        if capacity <= 0 or timeout_s <= 0:
            raise ValueError("Database pool size and timeout must be positive")
        self.connections: LifoQueue[sqlite3.Connection] = LifoQueue(capacity)
        self.lock = Lock()
        self.closed = False
        self.timeout_s = timeout_s
        identity: str | None = None
        try:
            for _ in range(capacity):
                connection = open_network_database(path, cache_kib, check_same_thread=False)
                self.connections.put_nowait(connection)
                candidate = network_metadata(connection).feature_identity
                if identity is not None and candidate != identity:
                    raise ValueError("Database generation changed while opening connections")
                identity = candidate
        except BaseException:
            self.close()
            raise

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            if self.closed:
                raise DatabaseBusyError("Path service is shutting down")
        try:
            connection = self.connections.get(timeout=self.timeout_s)
        except Empty as exc:
            raise DatabaseBusyError("Path service is busy; try again shortly") from exc
        try:
            yield connection
        finally:
            with self.lock:
                if self.closed:
                    connection.close()
                else:
                    self.connections.put_nowait(connection)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            while True:
                try:
                    self.connections.get_nowait().close()
                except Empty:
                    break

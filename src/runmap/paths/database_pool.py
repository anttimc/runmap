"""Bound read-only connections and lease each exclusively to one API request."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, LifoQueue
import sqlite3
from threading import Lock
from time import monotonic

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
    def connection(self, *, timeout_s: float | None = None,
                     deadline: float | None = None) -> Iterator[sqlite3.Connection]:
        with self.lock:
            if self.closed:
                raise DatabaseBusyError("Path service is shutting down")
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Path preparation exceeded its deadline; try a smaller budget")
            timeout_s = remaining if timeout_s is None else min(remaining, timeout_s)
        try:
            wait_s = self.timeout_s if timeout_s is None else min(self.timeout_s, max(0, timeout_s))
            connection = self.connections.get(timeout=wait_s)
        except Empty as exc:
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError("Path preparation exceeded its deadline; try a smaller budget") from exc
            raise DatabaseBusyError("Path service is busy; try again shortly") from exc
        try:
            if deadline is not None:
                connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            yield connection
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError("Path preparation exceeded its deadline; try a smaller budget")
        except sqlite3.OperationalError as exc:
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError("Path preparation exceeded its deadline; try a smaller budget") from exc
            raise
        finally:
            if deadline is not None:
                connection.set_progress_handler(None, 0)
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

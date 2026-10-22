"""SQLite dataset lifetime and a temporary lazy bridge for run planning."""

from pathlib import Path
from threading import Lock

from runmap.api.paths import PathData, load_path_data
from runmap.paths.coverage import geometry_bounds
from runmap.paths.database import dataset_presentation, network_metadata
from runmap.paths.database_pool import DatabasePool


class RoutingUnavailableError(RuntimeError):
    pass


class SQLitePathData:
    def __init__(self, path: Path, graph_path: Path, pool_size: int = 2, cache_kib: int = 16384) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)
        self.pool = DatabasePool(path, pool_size, cache_kib)
        try:
            with self.pool.connection() as connection:
                self.metadata = network_metadata(connection)
                self.presentation = dataset_presentation(connection)
            if (self.metadata.coverage is None or self.metadata.method is None
                    or self.metadata.measurement_sha256 is None):
                raise ValueError("Path API requires prepared measurements and coverage")
        except BaseException:
            self.pool.close()
            raise
        self.dataset_id = self.metadata.dataset_id
        self.coverage = self.metadata.coverage
        self.method = self.metadata.method
        self.measurement_sha256 = self.metadata.measurement_sha256
        self.bounds = geometry_bounds(self.coverage["geometry"]["coordinates"][0])
        self.graph_path = graph_path
        self.routing_lock = Lock()
        self.legacy: PathData | None = None
        self.routing_error: str | None = None
        self.closed = False

    def routing_data(self) -> PathData:
        """Retain compatibility until bounded SQLite graph searches replace this bridge."""
        with self.routing_lock:
            if self.closed:
                raise RoutingUnavailableError("Path service is shutting down")
            if self.routing_error is not None:
                raise RoutingUnavailableError(self.routing_error)
            if self.legacy is None:
                try:
                    data = load_path_data(self.graph_path)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    self.routing_error = "Routing is unavailable for this dataset"
                    raise RoutingUnavailableError(self.routing_error) from exc
                if (data.dataset_id != self.dataset_id
                        or data.graph["measurements"]["sha256"] != self.metadata.measurement_sha256):
                    self.routing_error = "Routing artifacts do not match the displayed dataset"
                    raise RoutingUnavailableError(self.routing_error)
                self.legacy = data
            return self.legacy

    def close(self) -> None:
        self.pool.close()
        with self.routing_lock:
            self.closed = True
            self.legacy = None

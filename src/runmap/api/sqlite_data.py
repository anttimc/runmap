"""SQLite dataset metadata and bounded connection lifetime."""

from pathlib import Path
from runmap.api.numeric_data import NumericRoutingData
from runmap.paths.coverage import geometry_bounds
from runmap.paths.database import dataset_presentation, network_metadata
from runmap.paths.database_pool import DatabasePool


class SQLitePathData:
    def __init__(self, path: Path, pool_size: int = 2, cache_kib: int = 16384, *, numeric_path: Path | None = None,
                 numeric_cache_bytes: int = 128 * 1024**2) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)
        self.pool = DatabasePool(path, pool_size, cache_kib)
        self.numeric: NumericRoutingData | None = None
        try:
            with self.pool.connection() as connection:
                self.metadata = network_metadata(connection)
                self.presentation = dataset_presentation(connection)
            if (self.metadata.coverage is None or self.metadata.method is None
                    or self.metadata.measurement_sha256 is None):
                raise ValueError("Path API requires prepared measurements and coverage")
            candidate = numeric_path or path.with_name("numeric-routing-v2.sqlite")
            if numeric_path is not None or candidate.is_file():
                self.numeric = NumericRoutingData(candidate, self.metadata, numeric_cache_bytes)
        except BaseException:
            self.pool.close()
            raise
        self.dataset_id = self.metadata.dataset_id
        self.coverage = self.metadata.coverage
        self.method = self.metadata.method
        self.measurement_sha256 = self.metadata.measurement_sha256
        self.bounds = geometry_bounds(self.coverage["geometry"]["coordinates"][0])

    def close(self) -> None:
        if self.numeric is not None:
            self.numeric.close()
            self.numeric = None
        self.pool.close()

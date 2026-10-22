"""SQLite dataset metadata and bounded connection lifetime."""

from pathlib import Path
from runmap.paths.coverage import geometry_bounds
from runmap.paths.database import dataset_presentation, network_metadata
from runmap.paths.database_pool import DatabasePool


class SQLitePathData:
    def __init__(self, path: Path, pool_size: int = 2, cache_kib: int = 16384) -> None:
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

    def close(self) -> None:
        self.pool.close()

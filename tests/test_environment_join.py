"""Check raster projection, partial traversals, and bounded join reads."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from runmap.paths.environment_join import (
    _schema,
    integrate_intervals,
    open_environment_join,
    project_segment,
    read_environment_segments,
)


def test_cell_join_preserves_chainage_nodata_and_partial_spans() -> None:
    bands = np.zeros((6, 1, 2), dtype=np.float32)
    bands[:, 0, 0] = (0.2, 0.5, 0.8, 0.1, 1, 1)
    bands[:, 0, 1] = (0.6, -9999, 0.4, 0.9, 0, 0)
    with MemoryFile() as memory, memory.open(
        driver="GTiff", height=1, width=2, count=6, dtype="float32",
        crs="EPSG:3067", transform=from_origin(0, 10, 10, 10), nodata=-9999,
    ) as raster:
        raster.write(bands)
        intervals = project_segment(
            "path", [(24.0, 60.0), (24.001, 60.0)], [(1, 5), (19, 5)],
            20, raster, (1, 2, 3, 4, 5, 6), {},
        )
    assert len(intervals) == 2
    assert [item.graph_start_m for item in intervals] == pytest.approx([0, 10])
    assert [item.graph_end_m for item in intervals] == pytest.approx([10, 20])
    assert [item.projected_length_m for item in intervals] == pytest.approx([9, 9])
    assert intervals[1].values[1] is None
    assert intervals[1].support == (0, 0)
    values, known = integrate_intervals(intervals, 5, 15)
    assert values == pytest.approx((0.4, 0.5, 0.6, 0.5))
    assert known == pytest.approx((9, 4.5, 9, 9))
    assert integrate_intervals(intervals, 0, 10)[0][0] == pytest.approx(0.2)
    assert integrate_intervals(intervals, 10, 20)[0][0] == pytest.approx(0.6)


def test_bounded_reader_keeps_numeric_segment_identity_and_missing_data() -> None:
    with sqlite3.connect(":memory:") as connection:
        _schema(connection)
        connection.executemany("INSERT INTO intervals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            (7, "a", 0, 0, 10, 10, 0.2, None, 0.8, 0.1, 1, 1),
            (7, "a", 1, 10, 20, 10, 0.6, None, 0.4, 0.9, 0, 0),
            (8, "b", 0, 0, 5, 5, None, None, None, None, None, None),
        ])
        selected = read_environment_segments(connection, [7, 8])
        assert list(selected) == [7, 8]
        assert [item.graph_start_m for item in selected[7]] == [0, 10]
        assert selected[8][0].values == (None, None, None, None)
        with pytest.raises(ValueError, match="lacks a selected segment"):
            read_environment_segments(connection, [9])


def test_diagonal_projection_agrees_with_direct_grid_sampling() -> None:
    bands = np.zeros((6, 2, 2), dtype=np.float32)
    bands[0] = ((0.1, 0.3), (0.7, 0.9))
    bands[1:] = 1
    first, second = (1.0, 18.0), (18.0, 5.0)
    with MemoryFile() as memory, memory.open(
        driver="GTiff", height=2, width=2, count=6, dtype="float32",
        crs="EPSG:3067", transform=from_origin(0, 20, 10, 10), nodata=-9999,
    ) as raster:
        raster.write(bands)
        intervals = project_segment(
            "diagonal", [(24.0, 60.0), (24.001, 60.0)], [first, second],
            20, raster, (1, 2, 3, 4, 5, 6), {},
        )
        fractions = (np.arange(10000) + 0.5) / 10000
        points = [(first[0] + t * (second[0] - first[0]),
                   first[1] + t * (second[1] - first[1])) for t in fractions]
        direct = np.mean([sample[0] for sample in raster.sample(points)])
    joined, known = integrate_intervals(intervals, 0, 20)
    assert joined[0] == pytest.approx(direct, abs=0.001)
    assert known[0] == pytest.approx(np.linalg.norm(np.subtract(second, first)))


def test_join_rejects_changed_source_identity(tmp_path: Path) -> None:
    path = tmp_path / "join.sqlite"
    identity = {"network_dataset_id": "network", "network_feature_identity": "features",
                "raster_dataset_id": "raster", "raster_sha256": "digest",
                "model": "environment-cell-projection-v1"}
    with closing(sqlite3.connect(path)) as connection:
        _schema(connection)
        connection.executemany("INSERT INTO metadata VALUES (?, ?)",
                               ((key, json.dumps(value)) for key, value in identity.items()))
        connection.commit()
    with pytest.raises(ValueError, match="identity mismatch"):
        open_environment_join(path, network_dataset_id="changed",
                              network_feature_identity="features", raster_dataset_id="raster",
                              raster_sha256="digest")
    with closing(open_environment_join(path, network_dataset_id="network",
                                       network_feature_identity="features", raster_dataset_id="raster",
                                       raster_sha256="digest")):
        pass

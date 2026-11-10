"""Project analytical environment raster cells onto physical path segments."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from tempfile import NamedTemporaryFile

import rasterio
from rasterio.io import DatasetReader

from runmap.environment_fields import EnvironmentField, EnvironmentValues, EnvironmentSupport, EnvironmentLengths
from runmap.paths.database import network_metadata, open_network_database
from runmap.paths.graph import geometry_length_m
from runmap.routing.types import Coordinate
from runmap.routing.deadlines import check_deadline
from runmap.sources.environment import file_identity

MODEL = "environment-cell-projection-v1"
FIELDS = EnvironmentValues._fields
SUPPORT = EnvironmentSupport._fields
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EnvironmentInterval:
    segment_id: str
    graph_start_m: float
    graph_end_m: float
    projected_length_m: float
    values: EnvironmentValues
    support: EnvironmentSupport


@dataclass(frozen=True)
class EnvironmentJoinIdentity:
    network_dataset_id: str
    network_feature_identity: str
    raster_dataset_id: str
    raster_sha256: str
    model: str = MODEL


def _bands(raster: DatasetReader, manifest: dict[str, object]) -> tuple[int, ...]:
    if raster.crs is None or raster.crs.to_epsg() != 3067 or raster.count != len(manifest["bands"]):
        raise ValueError("Environment raster layout does not match its manifest")
    if [raster.height, raster.width] != manifest["grid_shape"] or any(
        not math.isclose(actual, expected, abs_tol=1e-6)
        for actual, expected in zip(raster.bounds, manifest["bounds_epsg3067"])
    ):
        raise ValueError("Environment raster grid does not match its manifest")
    descriptions = tuple(raster.descriptions)
    if descriptions != tuple(manifest["bands"]):
        raise ValueError("Environment raster band descriptions do not match its manifest")
    missing = set(FIELDS + SUPPORT) - set(descriptions)
    if missing:
        raise ValueError(f"Environment raster lacks required bands: {sorted(missing)}")
    if raster.transform.b != 0 or raster.transform.d != 0 or raster.transform.a <= 0 or raster.transform.e >= 0:
        raise ValueError("Environment raster must have a north-up metric grid")
    return tuple(descriptions.index(name) + 1 for name in FIELDS + SUPPORT)


def _cuts(first: tuple[float, float], second: tuple[float, float], raster: DatasetReader) -> list[float]:
    """Split a projected edge wherever it crosses a pixel boundary."""
    transform = raster.transform
    a = ((first[0] - transform.c) / transform.a, (first[1] - transform.f) / transform.e)
    b = ((second[0] - transform.c) / transform.a, (second[1] - transform.f) / transform.e)
    fractions = [0.0, 1.0]
    for start, end in zip(a, b):
        if start == end:
            continue
        low, high = sorted((start, end))
        for boundary in range(math.floor(low) + 1, math.ceil(high)):
            fraction = (boundary - start) / (end - start)
            if 0.0 < fraction < 1.0:
                fractions.append(fraction)
    return sorted(set(fractions))


def _cell_values(raster: DatasetReader, bands: tuple[int, ...], x: float, y: float,
                 cache: dict[tuple[int, int], tuple[float | None, ...]]) -> tuple[float | None, ...]:
    row, column = raster.index(x, y)
    if not (0 <= row < raster.height and 0 <= column < raster.width):
        return (None,) * len(bands)
    key = (row, column)
    if key not in cache:
        pixel = raster.read(list(bands), window=((row, row + 1), (column, column + 1)), masked=True)
        cache[key] = tuple(None if bool(pixel.mask[i, 0, 0]) or not math.isfinite(float(pixel[i, 0, 0]))
                           else float(pixel[i, 0, 0]) for i in range(len(bands)))
    return cache[key]


def project_segment(segment_id: str, geometry: Sequence[Coordinate],
                    projected: Sequence[tuple[float, float]], graph_length_m: float,
                    raster: DatasetReader, bands: tuple[int, ...],
                    cache: dict[tuple[int, int], tuple[float | None, ...]]) -> tuple[EnvironmentInterval, ...]:
    """Keep graph chainage aligned with the terrain measurement convention."""
    if len(geometry) != len(projected) or len(geometry) < 2 or graph_length_m <= 0:
        raise ValueError(f"Invalid segment geometry: {segment_id}")
    geodesic_total = geometry_length_m(geometry)
    if geodesic_total <= 0:
        raise ValueError(f"Zero geodesic segment length: {segment_id}")
    intervals: list[EnvironmentInterval] = []
    chainage = 0.0
    for index, (first, second) in enumerate(pairwise(projected)):
        projected_length = math.dist(first, second)
        graph_step = (geometry_length_m(geometry[index:index + 2]) / geodesic_total
                      * graph_length_m)
        if projected_length > 0:
            cuts = _cuts(first, second, raster)
            for start, end in pairwise(cuts):
                middle = (start + end) / 2
                x = first[0] + middle * (second[0] - first[0])
                y = first[1] + middle * (second[1] - first[1])
                values = _cell_values(raster, bands, x, y, cache)
                intervals.append(EnvironmentInterval(
                    segment_id, chainage + start * graph_step, chainage + end * graph_step,
                    (end - start) * projected_length,
                    EnvironmentValues(*values[:len(FIELDS)]),
                    EnvironmentSupport(*values[len(FIELDS):]),
                ))
        chainage += graph_step
    if not intervals:
        raise ValueError(f"No projected length for segment: {segment_id}")
    last = intervals[-1]
    intervals[-1] = EnvironmentInterval(last.segment_id, last.graph_start_m, graph_length_m,
                                         last.projected_length_m, last.values, last.support)
    return tuple(intervals)


def integrate_intervals(intervals: Sequence[EnvironmentInterval], start_m: float,
                        end_m: float) -> tuple[EnvironmentValues, EnvironmentLengths]:
    """Average fields over one traversal span, retaining known-length coverage."""
    if start_m < 0 or end_m <= start_m or not intervals or end_m > intervals[-1].graph_end_m + 1e-6:
        raise ValueError("Invalid environment traversal span")
    sums = {field: 0.0 for field in EnvironmentField}
    lengths = {field: 0.0 for field in EnvironmentField}
    for interval in intervals:
        span = interval.graph_end_m - interval.graph_start_m
        overlap = max(0.0, min(end_m, interval.graph_end_m) - max(start_m, interval.graph_start_m))
        if overlap <= 0 or span <= 0:
            continue
        length = interval.projected_length_m * overlap / span
        for field in EnvironmentField:
            value = getattr(interval.values, field)
            if value is not None:
                sums[field] += value * length
                lengths[field] += length
    return (EnvironmentValues(**{field: sums[field] / lengths[field] if lengths[field] else None
                                 for field in EnvironmentField}), EnvironmentLengths(**lengths))


def _schema(connection: sqlite3.Connection) -> None:
    field_columns = ", ".join(f"{field} REAL" for field in FIELDS + SUPPORT)
    connection.executescript(f"""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE intervals (
            segment_number INTEGER NOT NULL, segment_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            graph_start_m REAL NOT NULL, graph_end_m REAL NOT NULL,
            projected_length_m REAL NOT NULL,
            {field_columns},
            PRIMARY KEY(segment_number, ordinal)
        ) WITHOUT ROWID;
    """)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _source_segments(connection: sqlite3.Connection) -> Iterator[tuple[int, str, Sequence[Coordinate],
                                                                     tuple[tuple[float, float], ...], float]]:
    for number, segment_id, payload, projected in connection.execute("""
        SELECT s.number, s.id, s.payload, f.projected_geometry FROM segments s
        JOIN segment_features f ON f.segment_id = s.id ORDER BY s.number
    """):
        segment = json.loads(payload)
        yield number, segment_id, segment["geometry"], tuple(tuple(point) for point in json.loads(projected)), segment["length_m"]


def _write_join(destination: Path, network: Path, bundle: Path) -> None:
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("schema_version") != 2:
        raise ValueError("Unsupported regional environment manifest")
    raster_path = bundle / "fields.tif"
    if file_identity(raster_path) != manifest["artifacts"][0]:
        raise ValueError("Environment raster checksum does not match its manifest")
    with closing(open_network_database(network)) as source, rasterio.open(raster_path) as raster, closing(sqlite3.connect(destination)) as target:
        metadata = network_metadata(source)
        bands = _bands(raster, manifest)
        identity = EnvironmentJoinIdentity(metadata.dataset_id, metadata.feature_identity,
                                           manifest["dataset_id"], manifest["artifacts"][0]["sha256"])
        _schema(target)
        target.executemany("INSERT INTO metadata VALUES (?, ?)",
                           ((key, json.dumps(value)) for key, value in asdict(identity).items()))
        cache: dict[tuple[int, int], tuple[float | None, ...]] = {}
        with target:
            for number, segment_id, geometry, projected, length in _source_segments(source):
                intervals = project_segment(segment_id, geometry, projected, length, raster, bands, cache)
                placeholders = ", ".join("?" for _ in range(6 + len(FIELDS) + len(SUPPORT)))
                target.executemany(f"INSERT INTO intervals VALUES ({placeholders})",
                                   ((number, segment_id, ordinal, item.graph_start_m, item.graph_end_m,
                                     item.projected_length_m, *item.values, *item.support)
                                    for ordinal, item in enumerate(intervals)))
                if len(cache) > 65536:
                    cache.clear()


def export_environment_join(network: Path, bundle: Path, destination: Path) -> None:
    """Publish a complete immutable join after source identity validation."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, suffix=".sqlite", delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        _write_join(staging, network, bundle)
        os.link(staging, destination)
    finally:
        staging.unlink(missing_ok=True)


def open_environment_join(path: Path, *, network_dataset_id: str,
                          network_feature_identity: str, raster_dataset_id: str,
                          raster_sha256: str) -> sqlite3.Connection:
    """Reject a join prepared from a different graph or analytical raster."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                 check_same_thread=False)
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("Unsupported environment join schema")
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        expected = {"network_dataset_id": network_dataset_id,
                    "network_feature_identity": network_feature_identity,
                    "raster_dataset_id": raster_dataset_id,
                    "raster_sha256": raster_sha256, "model": MODEL}
        if any(json.loads(metadata[key]) != value for key, value in expected.items()):
            raise ValueError("Environment join source or model identity mismatch")
        return connection
    except BaseException:
        connection.close()
        raise


def read_environment_segments(connection: sqlite3.Connection,
                              segment_numbers: Sequence[int], *,
                              deadline: float | None = None) -> dict[int, tuple[EnvironmentInterval, ...]]:
    """Load only requested physical segments for a bounded routing window."""
    result: dict[int, list[EnvironmentInterval]] = {number: [] for number in segment_numbers}
    for offset in range(0, len(segment_numbers), 500):
        check_deadline(deadline)
        batch = segment_numbers[offset:offset + 500]
        placeholders = ",".join("?" for _ in batch)
        field_columns = ", ".join(FIELDS + SUPPORT)
        cursor = connection.execute(f"""SELECT segment_number, segment_id, graph_start_m, graph_end_m,
            projected_length_m, {field_columns} FROM intervals WHERE segment_number IN ({placeholders})
            ORDER BY segment_number, ordinal""", batch)
        for number, segment_id, start_m, end_m, projected_length_m, *measurements in cursor:
            result[number].append(EnvironmentInterval(
                segment_id, start_m, end_m, projected_length_m,
                EnvironmentValues(*measurements[:len(FIELDS)]),
                EnvironmentSupport(*measurements[len(FIELDS):])))
        check_deadline(deadline)
    if any(not value for value in result.values()):
        raise ValueError("Environment join lacks a selected segment")
    return {key: tuple(value) for key, value in result.items()}


def read_environment_by_segment_ids(connection: sqlite3.Connection, network: sqlite3.Connection,
                                     segment_ids: Sequence[str], *, deadline: float | None = None
                                     ) -> dict[str, tuple[EnvironmentInterval, ...]]:
    """Resolve bounded reference graph IDs through the prepared network identity table."""
    numbers: list[int] = []
    for offset in range(0, len(segment_ids), 500):
        check_deadline(deadline)
        batch = segment_ids[offset:offset + 500]
        placeholders = ",".join("?" for _ in batch)
        numbers.extend(row[0] for row in network.execute(
            f"SELECT number FROM segments WHERE id IN ({placeholders})", batch))
    intervals = read_environment_segments(connection, numbers, deadline=deadline)
    result = {rows[0].segment_id: rows for rows in intervals.values()}
    if result.keys() != set(segment_ids):
        raise ValueError("Environment join lacks a matching physical segment")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", type=Path)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export_environment_join(args.network, args.bundle, args.output)
    print(args.output)


if __name__ == "__main__":
    main()

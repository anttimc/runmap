"""Prepared SQLite network windows; geometry processing stays in Python."""

from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from typing import cast

from rasterio.warp import transform

from runmap.paths.measurements import graph_digest, load_measurements, segment_measurements
from runmap.routing.serialization import prepared_json
from runmap.routing.types import Arc, Bounds, GraphDocument, Measurement, Node, Segment, TerrainMetadata


SCHEMA_VERSION = 1
SELECTION = """
    SELECT s.* FROM segment_bounds b JOIN segments s ON s.number = b.number
    WHERE b.west <= :east AND b.east >= :west
      AND b.south <= :north AND b.north >= :south
"""


@dataclass(frozen=True)
class NetworkMetadata:
    dataset_id: str
    measurement_sha256: str | None
    method: TerrainMetadata | None


@dataclass(frozen=True)
class NetworkWindow:
    bounds: Bounds
    graph: GraphDocument
    measurements: dict[str, list[Measurement]]
    frontier_nodes: tuple[str, ...]


def _encode(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False, default=prepared_json)


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE nodes (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE segments (
            number INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL REFERENCES nodes(id),
            target TEXT NOT NULL REFERENCES nodes(id), payload TEXT NOT NULL
        );
        CREATE INDEX segments_source ON segments(source);
        CREATE INDEX segments_target ON segments(target);
        CREATE VIRTUAL TABLE segment_bounds USING rtree(number, west, east, south, north);
        CREATE TABLE arcs (
            number INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
            segment_id TEXT NOT NULL REFERENCES segments(id),
            source TEXT NOT NULL REFERENCES nodes(id),
            target TEXT NOT NULL REFERENCES nodes(id), payload TEXT NOT NULL
        );
        CREATE INDEX arcs_segment ON arcs(segment_id);
        CREATE INDEX arcs_source ON arcs(source);
        CREATE TABLE measurements (
            segment_id TEXT PRIMARY KEY REFERENCES segments(id), payload TEXT NOT NULL
        );
    """)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _segment_rows(graph: GraphDocument) -> Iterator[tuple[int, str, str, str, str, Bounds]]:
    for number, segment in enumerate(graph["segments"]):
        coordinates = segment["geometry"]
        if not coordinates:
            raise ValueError("Segment geometry is empty")
        longitude, latitude = zip(*coordinates)
        eastings, northings = transform("EPSG:4326", "EPSG:3067", longitude, latitude)
        bounds = min(eastings), min(northings), max(eastings), max(northings)
        _validate_bounds(bounds)
        yield number, segment["id"], segment["from"], segment["to"], _encode(segment), bounds


def _write_network(connection: sqlite3.Connection, graph_path: Path) -> None:
    graph = cast(GraphDocument, json.loads(graph_path.read_text()))
    if graph.get("schema_version", 1) != 1:
        raise ValueError("Unsupported graph schema")
    dataset_id = graph_digest(graph)
    measurements = load_measurements(graph_path, graph=graph) if "measurements" in graph else None
    reference = cast(dict[str, str | int], graph.get("measurements", {}))
    metadata = {"dataset_id": dataset_id, "measurement_sha256": reference.get("sha256"),
                "method": measurements.method if measurements else None, "crs": "EPSG:3067"}
    connection.executemany("INSERT INTO metadata VALUES (?, ?)",
                           ((key, _encode(value)) for key, value in metadata.items()))
    connection.execute("INSERT INTO metadata VALUES (?, ?)", ("graph_metadata", _encode({
        key: value for key, value in graph.items()
        if key not in {"nodes", "segments", "arcs", "_measurements"}
    })))
    connection.executemany("INSERT INTO nodes VALUES (?, ?)",
                           ((node["id"], _encode(node)) for node in graph["nodes"]))
    for number, segment_id, source, target, payload, bounds in _segment_rows(graph):
        connection.execute("INSERT INTO segments VALUES (?, ?, ?, ?, ?)",
                           (number, segment_id, source, target, payload))
        west, south, east, north = bounds
        connection.execute("INSERT INTO segment_bounds VALUES (?, ?, ?, ?, ?)",
                           (number, west, east, south, north))
        if measurements is not None:
            connection.execute("INSERT INTO measurements VALUES (?, ?)",
                               (segment_id, _encode(segment_measurements(measurements, segment_id))))
    connection.executemany("INSERT INTO arcs VALUES (?, ?, ?, ?, ?, ?)",
                           ((number, arc["id"], arc["segment_id"], arc["from"], arc["to"], _encode(arc))
                            for number, arc in enumerate(graph["arcs"])))


def export_network_database(graph_path: Path, destination: Path) -> None:
    """Publish a new database only after the complete import succeeds."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, suffix=".sqlite", delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        with closing(sqlite3.connect(staging)) as connection:
            _create_schema(connection)
            with connection:
                _write_network(connection, graph_path)
        # A hard link publishes atomically and refuses to overwrite an existing dataset.
        os.link(staging, destination)
    finally:
        staging.unlink(missing_ok=True)


def open_network_database(path: Path, cache_kib: int = 16384) -> sqlite3.Connection:
    """Return a caller-owned read-only connection with a bounded page-cache target."""
    if cache_kib <= 0:
        raise ValueError("Page-cache target must be positive")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("Unsupported network database schema")
        connection.execute(f"PRAGMA cache_size = {-cache_kib}")
        return connection
    except BaseException:
        connection.close()
        raise


def network_metadata(connection: sqlite3.Connection) -> NetworkMetadata:
    values = {key: json.loads(value) for key, value in connection.execute(
        "SELECT key, value FROM metadata WHERE key != 'graph_metadata'")}
    if values["crs"] != "EPSG:3067":
        raise ValueError("Unsupported network database CRS")
    return NetworkMetadata(values["dataset_id"], values["measurement_sha256"], values["method"])


def _validate_bounds(bounds: Bounds) -> None:
    west, south, east, north = bounds
    if not all(math.isfinite(value) for value in bounds) or west > east or south > north:
        raise ValueError("Expected finite ordered EPSG:3067 bounds")


def query_network(connection: sqlite3.Connection, bounds: Bounds,
                  *, include_measurements: bool = True) -> NetworkWindow:
    """Select complete segments by projected envelope, retaining unloaded-connection markers."""
    _validate_bounds(bounds)
    west, south, east, north = bounds
    parameters = dict(west=west, south=south, east=east, north=north)
    prefix = f"WITH selected AS ({SELECTION}) "
    segments = [cast(Segment, json.loads(row[0])) for row in connection.execute(
        prefix + "SELECT payload FROM selected ORDER BY number", parameters)]
    nodes = [cast(Node, json.loads(row[0])) for row in connection.execute(prefix + """
        SELECT n.payload FROM nodes n JOIN (
            SELECT source AS id FROM selected UNION SELECT target FROM selected
        ) endpoints ON endpoints.id = n.id ORDER BY n.id
    """, parameters)]
    arcs = [cast(Arc, json.loads(row[0])) for row in connection.execute(prefix + """
        SELECT a.payload FROM arcs a JOIN selected s ON s.id = a.segment_id ORDER BY a.number
    """, parameters)]
    frontier = tuple(row[0] for row in connection.execute(prefix + """
        , endpoints AS (SELECT source AS id FROM selected UNION SELECT target FROM selected)
        SELECT e.id FROM endpoints e WHERE EXISTS (
            SELECT 1 FROM segments s WHERE s.source = e.id
                AND NOT EXISTS (SELECT 1 FROM selected picked WHERE picked.number = s.number)
        ) OR EXISTS (
            SELECT 1 FROM segments s WHERE s.target = e.id
                AND NOT EXISTS (SELECT 1 FROM selected picked WHERE picked.number = s.number)
        ) ORDER BY e.id
    """, parameters))
    measurements = _query_measurements(connection, prefix, parameters) if include_measurements else {}
    return NetworkWindow(bounds, GraphDocument(nodes=nodes, segments=segments, arcs=arcs), measurements, frontier)


def _query_measurements(connection: sqlite3.Connection, prefix: str,
                        parameters: dict[str, float]) -> dict[str, list[Measurement]]:
    return {row[0]: cast(list[Measurement], json.loads(row[1])) for row in connection.execute(prefix + """
        SELECT m.segment_id, m.payload FROM measurements m
        JOIN selected s ON s.id = m.segment_id ORDER BY s.number
    """, parameters)}

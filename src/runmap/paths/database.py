"""Prepared SQLite network windows; geometry processing stays in Python."""

from contextlib import closing
from collections.abc import Iterator
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from typing import cast

from runmap.paths.coverage import Coverage, geometry_bounds
from runmap.paths.database_features import (create_feature_schema, read_display_intervals,
                                           read_segment_features, write_prepared_features, iter_display_intervals,
                                           decode_classification)
from runmap.paths.measurements import graph_digest, segment_measurements
from runmap.paths.preparation import (PreparedDisplayInterval, PreparedModels, PreparedNetwork,
                                      PreparedSegment, PreparedPolicy, prepare_network, prepared_models)
from runmap.paths.dead_ends import Classification
from runmap.routing.geometry import indexed_segment_points
from runmap.routing.hill_types import HillModel
from runmap.routing.serialization import prepared_json
from runmap.routing.types import Arc, Bounds, EdgeProfile, GraphDocument, GraphSource, Measurement, Node, Segment, TerrainMetadata


SCHEMA_VERSION = 2
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
    feature_identity: str
    models: PreparedModels
    coverage: Coverage | None
    distance_scale: float
    hill_valid_fraction: float | None
    hill_trusted_fraction: float | None


@dataclass(frozen=True)
class NetworkWindow:
    bounds: Bounds
    graph: GraphDocument
    measurements: dict[str, list[Measurement]]
    frontier_nodes: tuple[str, ...]


@dataclass(frozen=True)
class DatasetPresentation:
    source: GraphSource
    longitude: float | None
    latitude: float | None
    hidden_segments: int
    hidden_intervals: int


@dataclass(frozen=True)
class SegmentInspection:
    segment: Segment
    measurements: list[Measurement]
    surface_class: str
    building_access: Classification | None
    policy: PreparedPolicy


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
            number INTEGER PRIMARY KEY, segment_id TEXT NOT NULL REFERENCES segments(id),
            interval_index INTEGER NOT NULL, payload TEXT NOT NULL,
            UNIQUE(segment_id, interval_index)
        );
    """)
    create_feature_schema(connection)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _write_network(connection: sqlite3.Connection, graph_path: Path) -> None:
    graph = cast(GraphDocument, json.loads(graph_path.read_text()))
    if graph.get("schema_version", 1) != 1:
        raise ValueError("Unsupported graph schema")
    dataset_id = graph_digest(graph)
    _write_topology(connection, graph)
    prepared = prepare_network(graph, graph_path)
    _write_metadata(connection, graph, dataset_id, prepared)
    _write_bounds_and_measurements(connection, prepared)
    write_prepared_features(connection, prepared)


def _write_metadata(connection: sqlite3.Connection, graph: GraphDocument,
                    dataset_id: str, prepared: PreparedNetwork) -> None:
    measurements, hills = prepared.measurements, prepared.hills
    reference = cast(dict[str, str | int], graph.get("measurements", {}))
    models = prepared_models(hills.model if hills else None)
    metadata = {"dataset_id": dataset_id, "measurement_sha256": reference.get("sha256"),
                "method": measurements.method if measurements else None, "crs": "EPSG:3067",
                "models": asdict(models), "coverage": prepared.coverage,
                "distance_scale": prepared.routing.projected.distance_scale,
                "hill_valid_fraction": hills.valid_fraction if hills else None,
                "hill_trusted_fraction": hills.trusted_fraction if hills else None,
                "feature_identity": _feature_identity(dataset_id, cast(str | None, reference.get("sha256")), models)}
    connection.executemany("INSERT INTO metadata VALUES (?, ?)",
                           ((key, _encode(value)) for key, value in metadata.items()))
    connection.execute("INSERT INTO metadata VALUES (?, ?)", ("graph_metadata", _encode({
        key: value for key, value in graph.items()
        if key not in {"nodes", "segments", "arcs", "_measurements"}
    })))
    _write_presentation(connection, graph, prepared)


def _write_presentation(connection: sqlite3.Connection, graph: GraphDocument, prepared: PreparedNetwork) -> None:
    start = cast(dict[str, object], graph.get("start", {}))
    measurements = prepared.measurements
    hidden_intervals = sum(int(measurements.interval_offsets[i + 1] - measurements.interval_offsets[i])
                           for segment in prepared.building_access
                           for i in (measurements.segment_indices[segment],)) if measurements is not None else 0
    value = {"source": graph.get("source", {}), "longitude": start.get("longitude"),
             "latitude": start.get("latitude"), "hidden_segments": len(prepared.building_access),
             "hidden_intervals": hidden_intervals}
    connection.execute("INSERT INTO metadata VALUES ('presentation', ?)", (_encode(value),))


def _write_bounds_and_measurements(connection: sqlite3.Connection, prepared: PreparedNetwork) -> None:
    routing, measurements = prepared.routing, prepared.measurements
    for number, segment_id in enumerate(routing.segments):
        west, south, east, north = geometry_bounds(indexed_segment_points(routing.projected, number))
        _validate_bounds((west, south, east, north))
        connection.execute("INSERT INTO segment_bounds VALUES (?, ?, ?, ?, ?)",
                           (number, west, east, south, north))
        if measurements is not None:
            offset = int(measurements.interval_offsets[number])
            connection.executemany("INSERT INTO measurements VALUES (?, ?, ?, ?)", (
                (offset + local, segment_id, local, _encode(item))
                for local, item in enumerate(segment_measurements(measurements, segment_id))))


def _write_topology(connection: sqlite3.Connection, graph: GraphDocument) -> None:
    connection.executemany("INSERT INTO nodes VALUES (?, ?)",
                           ((node["id"], _encode(node)) for node in graph["nodes"]))
    connection.executemany("INSERT INTO segments VALUES (?, ?, ?, ?, ?)",
                           ((number, segment["id"], segment["from"], segment["to"], _encode(segment))
                            for number, segment in enumerate(graph["segments"])))
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


def open_network_database(path: Path, cache_kib: int = 16384, *,
                          check_same_thread: bool = True) -> sqlite3.Connection:
    """Return a caller-owned read-only connection with a bounded page-cache target."""
    if cache_kib <= 0:
        raise ValueError("Page-cache target must be positive")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=check_same_thread)
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("Unsupported network database schema")
        connection.execute(f"PRAGMA cache_size = {-cache_kib}")
        network_metadata(connection)
        return connection
    except BaseException:
        connection.close()
        raise


def network_metadata(connection: sqlite3.Connection) -> NetworkMetadata:
    values = {key: json.loads(value) for key, value in connection.execute(
        "SELECT key, value FROM metadata WHERE key != 'graph_metadata'")}
    if values["crs"] != "EPSG:3067":
        raise ValueError("Unsupported network database CRS")
    models = _decode_models(values["models"])
    if models != prepared_models(HillModel() if models.hill_model is not None else None):
        raise ValueError("Prepared feature models changed; re-export the network database")
    identity = _feature_identity(values["dataset_id"], values["measurement_sha256"], models)
    if identity != values["feature_identity"]:
        raise ValueError("Prepared feature identity mismatch")
    return NetworkMetadata(values["dataset_id"], values["measurement_sha256"], values["method"],
                           identity, models, values["coverage"], values["distance_scale"],
                           values["hill_valid_fraction"], values["hill_trusted_fraction"])


def _decode_models(value: dict[str, object]) -> PreparedModels:
    return PreparedModels(
        version=cast(str, value["version"]), flat_version=cast(str, value["flat_version"]),
        flat_minimum_length_m=cast(float, value["flat_minimum_length_m"]),
        flat_grade_thresholds=tuple(cast(list[float], value["flat_grade_thresholds"])),
        coverage_version=cast(str, value["coverage_version"]),
        classifier_version=cast(str, value["classifier_version"]), access_policy=cast(str, value["access_policy"]),
        entrance_limit_m=cast(float, value["entrance_limit_m"]), policy_version=cast(str, value["policy_version"]),
        boundary_version=cast(str, value["boundary_version"]),
        profiles=tuple(cast(list[EdgeProfile], value["profiles"])),
        hill_model=_decode_hill_model(cast(dict[str, object] | None, value["hill_model"])),
    )


def _decode_hill_model(value: dict[str, object] | None) -> HillModel | None:
    if value is None:
        return None
    return HillModel(
        version=cast(str, value["version"]), source_doi=cast(str, value["source_doi"]),
        coefficients=tuple(cast(list[float], value["coefficients"])),
        fitted_grade_limit=cast(float, value["fitted_grade_limit"]),
        outside_fit_policy=cast(str, value["outside_fit_policy"]),
        uncertain_grade_percent=cast(float, value["uncertain_grade_percent"]),
        uphill_reference_grade_percent=cast(float, value["uphill_reference_grade_percent"]),
        uphill_grade_power=cast(float, value["uphill_grade_power"]),
    )


def _feature_identity(dataset_id: str, checksum: str | None, models: PreparedModels) -> str:
    payload = json.dumps([dataset_id, checksum, asdict(models)], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


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
    result: dict[str, list[Measurement]] = {}
    for row in connection.execute(prefix + """
        SELECT m.segment_id, m.payload FROM measurements m
        JOIN selected s ON s.id = m.segment_id ORDER BY s.number, m.interval_index
    """, parameters):
        result.setdefault(row[0], []).append(cast(Measurement, json.loads(row[1])))
    # Preserve empty measurement lists for segments with no samples.
    if network_metadata(connection).method is not None:
        for row in connection.execute(prefix + "SELECT id FROM selected ORDER BY number", parameters):
            result.setdefault(row[0], [])
    return result


def query_segment_features(connection: sqlite3.Connection, bounds: Bounds) -> dict[str, PreparedSegment]:
    """Fetch global features for complete segment-envelope candidates."""
    parameters = _query_parameters(bounds)
    return read_segment_features(connection, f"WITH selected AS ({SELECTION}) ", parameters)


def query_display_intervals(connection: sqlite3.Connection, bounds: Bounds,
                            *, hide_building_access: bool = True) -> tuple[PreparedDisplayInterval, ...]:
    """Read individual interval candidates without loading their whole segments."""
    return read_display_intervals(connection, _query_parameters(bounds), hide_building_access)


def query_projected_nodes(connection: sqlite3.Connection, bounds: Bounds) -> dict[str, tuple[float, float]]:
    """Fetch projected endpoints, including those outside the selected box."""
    return {row[0]: (row[1], row[2]) for row in connection.execute(f"WITH selected AS ({SELECTION}) " + """
        SELECT p.node_id, p.x, p.y FROM projected_nodes p JOIN (
            SELECT source AS id FROM selected UNION SELECT target FROM selected
        ) e ON e.id = p.node_id ORDER BY p.node_id
    """, _query_parameters(bounds))}


def _query_parameters(bounds: Bounds) -> dict[str, float]:
    _validate_bounds(bounds)
    west, south, east, north = bounds
    return dict(west=west, south=south, east=east, north=north)


def dataset_presentation(connection: sqlite3.Connection) -> DatasetPresentation:
    row = connection.execute("SELECT value FROM metadata WHERE key = 'presentation'").fetchone()
    if row is None:
        raise ValueError("API presentation metadata is missing; re-export the network database")
    value = json.loads(row[0])
    return DatasetPresentation(value["source"], value["longitude"], value["latitude"],
                               value["hidden_segments"], value["hidden_intervals"])


def iter_query_display_intervals(connection: sqlite3.Connection, bounds: Bounds, policy_id: str,
                                 *, hide_building_access: bool = True) -> Iterator[PreparedDisplayInterval]:
    return iter_display_intervals(connection, _query_parameters(bounds), hide_building_access, policy_id)


def query_segment_inspection(connection: sqlite3.Connection, segment_id: str, policy_id: str) -> SegmentInspection:
    row = connection.execute("""
        SELECT s.payload, f.surface_class, f.building_access, p.forward, p.backward, p.reason
        FROM segments s JOIN segment_features f ON f.segment_id = s.id
        JOIN segment_policies p ON p.segment_id = s.id AND p.policy_id = ? WHERE s.id = ?
    """, (policy_id, segment_id)).fetchone()
    if row is None:
        raise KeyError(segment_id)
    measurements = [cast(Measurement, json.loads(item[0])) for item in connection.execute(
        "SELECT payload FROM measurements WHERE segment_id = ? ORDER BY interval_index", (segment_id,))]
    return SegmentInspection(cast(Segment, json.loads(row[0])), measurements, row[1], decode_classification(row[2]),
                              PreparedPolicy(policy_id, bool(row[3]), bool(row[4]), row[5]))


def query_segment_record(connection: sqlite3.Connection, segment_id: str) -> Segment:
    row = connection.execute("SELECT payload FROM segments WHERE id = ?", (segment_id,)).fetchone()
    if row is None:
        raise KeyError(segment_id)
    return cast(Segment, json.loads(row[0]))

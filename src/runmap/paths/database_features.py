"""SQLite persistence for globally prepared display and routing features."""

from dataclasses import asdict
import json
import sqlite3
from typing import cast

from runmap.paths.coverage import geometry_bounds, near_coverage_boundary
from runmap.paths.dead_ends import Classification
from runmap.paths.flat import measurement_flat_thresholds
from runmap.paths.measurements import segment_measurements
from runmap.paths.preparation import (PreparedDisplayInterval, PreparedNetwork, PreparedPolicy,
                                      PreparedSegment, prepared_models)
from runmap.routing.geometry import indexed_node_point, indexed_segment_points, projected_points
from runmap.routing.hill_types import HillData, HillFeatures, HillInterval
from runmap.routing.profiles import surface_category
from runmap.routing.search import apply_profile
from runmap.routing.types import Measurement


def create_feature_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE projected_nodes (
            node_id TEXT PRIMARY KEY REFERENCES nodes(id), x REAL NOT NULL, y REAL NOT NULL
        );
        CREATE TABLE segment_features (
            segment_id TEXT PRIMARY KEY REFERENCES segments(id), surface_class TEXT NOT NULL,
            boundary_blocked INTEGER NOT NULL CHECK(boundary_blocked IN (0, 1)),
            building_access TEXT, projected_geometry TEXT NOT NULL
        );
        CREATE TABLE segment_policies (
            segment_id TEXT NOT NULL REFERENCES segments(id), policy_id TEXT NOT NULL,
            forward INTEGER NOT NULL CHECK(forward IN (0, 1)),
            backward INTEGER NOT NULL CHECK(backward IN (0, 1)), reason TEXT,
            PRIMARY KEY(segment_id, policy_id)
        );
        CREATE TABLE display_intervals (
            number INTEGER PRIMARY KEY REFERENCES measurements(number),
            flat_min_grade_50m REAL, coverage_limited INTEGER NOT NULL CHECK(coverage_limited IN (0, 1))
        );
        CREATE VIRTUAL TABLE interval_bounds USING rtree(number, west, east, south, north);
        CREATE TABLE hill_features (
            segment_id TEXT PRIMARY KEY REFERENCES segments(id),
            intervals TEXT NOT NULL, forward TEXT NOT NULL, backward TEXT NOT NULL
        );
    """)


def encode_feature(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def write_prepared_features(connection: sqlite3.Connection, prepared: PreparedNetwork) -> None:
    routing = prepared.routing
    index = routing.integer
    if index is None:
        raise ValueError("Prepared features require an integer graph")
    connection.executemany("INSERT INTO projected_nodes VALUES (?, ?, ?)",
                           ((node_id, *indexed_node_point(routing.projected, number))
                            for number, node_id in enumerate(index.node_ids)))
    for number, segment_id in enumerate(index.segment_ids):
        classification = prepared.building_access.get(segment_id)
        connection.execute("INSERT INTO segment_features VALUES (?, ?, ?, ?, ?)", (
            segment_id, surface_category(routing.segments[segment_id]),
            segment_id in routing.boundary_blocked_segments,
            encode_feature(asdict(classification)) if classification else None,
            encode_feature(indexed_segment_points(routing.projected, number)),
        ))
        if prepared.hills is not None:
            _write_hills(connection, prepared.hills, number, segment_id)
    _write_policies(connection, prepared)
    _write_display_intervals(connection, prepared)


def _write_policies(connection: sqlite3.Connection, prepared: PreparedNetwork) -> None:
    for profile in prepared_models(None).profiles:
        index = apply_profile(prepared.routing, profile)
        for segment_id in index.segments:
            reason = index.ineligible_segments.get(segment_id)
            if reason is None and segment_id in index.boundary_blocked_segments:
                reason = "prepared_route_boundary"
            directions = index.direction_cache[segment_id]
            if reason is None and not directions:
                reason = "no_legal_direction"
            connection.execute("INSERT INTO segment_policies VALUES (?, ?, ?, ?, ?)", (
                segment_id, profile["id"], reason is None and True in directions,
                reason is None and False in directions, reason,
            ))


def _write_display_intervals(connection: sqlite3.Connection, prepared: PreparedNetwork) -> None:
    measurements = prepared.measurements
    if measurements is None:
        return
    for segment_number, segment_id in enumerate(measurements.segment_ids):
        intervals = segment_measurements(measurements, segment_id)
        thresholds = measurement_flat_thresholds(intervals)
        offset = int(measurements.interval_offsets[segment_number])
        for local, (item, threshold) in enumerate(zip(intervals, thresholds)):
            number = offset + local
            limited = prepared.coverage is None or near_coverage_boundary(prepared.coverage, item["geometry"])
            connection.execute("INSERT INTO display_intervals VALUES (?, ?, ?)", (number, threshold, limited))
            west, south, east, north = geometry_bounds(projected_points(item["geometry"]))
            connection.execute("INSERT INTO interval_bounds VALUES (?, ?, ?, ?, ?)",
                               (number, west, east, south, north))


def _write_hills(connection: sqlite3.Connection, hills: HillData, number: int, segment_id: str) -> None:
    start, end = int(hills.interval_offsets[number]), int(hills.interval_offsets[number + 1])
    intervals = [HillInterval(float(hills.chainage[i, 0]), float(hills.chainage[i, 1]),
                              HillFeatures(*map(float, hills.features[i, 0])),
                              HillFeatures(*map(float, hills.features[i, 1])))
                 for i in range(start, end)]
    connection.execute("INSERT INTO hill_features VALUES (?, ?, ?, ?)", (
        segment_id, encode_feature([asdict(item) for item in intervals]),
        encode_feature(asdict(HillFeatures(*map(float, hills.totals[number, 0])))),
        encode_feature(asdict(HillFeatures(*map(float, hills.totals[number, 1])))),
    ))


def decode_classification(payload: str | None) -> Classification | None:
    if payload is None:
        return None
    value = json.loads(payload)
    return Classification(value["reason"], value["branch_id"], value["attachment_node"],
                          value["branch_length_m"], tuple(value["evidence"]))


def read_segment_features(connection: sqlite3.Connection, prefix: str,
                          parameters: dict[str, float]) -> dict[str, PreparedSegment]:
    policies: dict[str, list[PreparedPolicy]] = {}
    for row in connection.execute(prefix + """
        SELECT p.segment_id, p.policy_id, p.forward, p.backward, p.reason
        FROM segment_policies p JOIN selected s ON s.id = p.segment_id ORDER BY s.number, p.policy_id
    """, parameters):
        policies.setdefault(row[0], []).append(PreparedPolicy(row[1], bool(row[2]), bool(row[3]), row[4]))
    return {row[0]: _read_segment(row, tuple(policies.get(row[0], ()))) for row in connection.execute(prefix + """
        SELECT f.segment_id, f.surface_class, f.boundary_blocked, f.building_access, f.projected_geometry,
               h.intervals, h.forward, h.backward
        FROM segment_features f JOIN selected s ON s.id = f.segment_id
        LEFT JOIN hill_features h ON h.segment_id = f.segment_id ORDER BY s.number
    """, parameters)}


def _read_segment(row: tuple[str, str, int, str | None, str, str | None, str | None, str | None],
                  policies: tuple[PreparedPolicy, ...]) -> PreparedSegment:
    intervals = tuple(HillInterval(item["graph_start_m"], item["graph_end_m"],
                                    HillFeatures(**item["forward"]), HillFeatures(**item["backward"]))
                      for item in json.loads(row[5])) if row[5] is not None else ()
    return PreparedSegment(
        row[0], row[1], bool(row[2]), decode_classification(row[3]),
        tuple((float(x), float(y)) for x, y in json.loads(row[4])), policies, intervals,
        HillFeatures(**json.loads(row[6])) if row[6] is not None else None,
        HillFeatures(**json.loads(row[7])) if row[7] is not None else None,
    )


def read_display_intervals(connection: sqlite3.Connection, parameters: dict[str, float],
                           hide_building_access: bool) -> tuple[PreparedDisplayInterval, ...]:
    return tuple(PreparedDisplayInterval(cast(Measurement, json.loads(row[0])), row[1], row[2],
                                         bool(row[3]), decode_classification(row[4]))
                 for row in connection.execute("""
        SELECT m.payload, f.surface_class, d.flat_min_grade_50m, d.coverage_limited, f.building_access
        FROM interval_bounds b JOIN display_intervals d ON d.number = b.number
        JOIN measurements m ON m.number = d.number
        JOIN segment_features f ON f.segment_id = m.segment_id
        WHERE b.west <= :east AND b.east >= :west AND b.south <= :north AND b.north >= :south
          AND (:hide = 0 OR f.building_access IS NULL) ORDER BY d.number
    """, {**parameters, "hide": int(hide_building_access)}))

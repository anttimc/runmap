"""Spatial source-signal evidence for map inspection, independent of route attribution."""

from dataclasses import dataclass
import json
import sqlite3

from runmap.paths.signal_preparation import signal_evidence
from runmap.routing.types import Bounds, Tags


@dataclass(frozen=True)
class SignalPoint:
    osm_id: str
    longitude: float
    latitude: float
    tags: Tags


def signal_point_kind(tags: Tags) -> str:
    if signal_evidence(tags) and not tags.get("railway") and tags.get("crossing:signals") != "no":
        return "pedestrian"
    return "uncertain"


def write_signal_points(connection: sqlite3.Connection, points: tuple[SignalPoint, ...]) -> None:
    connection.executescript("""
        CREATE TABLE signal_points (number INTEGER PRIMARY KEY, osm_id TEXT UNIQUE NOT NULL,
            longitude REAL NOT NULL, latitude REAL NOT NULL, kind TEXT NOT NULL, tags TEXT NOT NULL);
        CREATE VIRTUAL TABLE signal_point_bounds USING rtree(number, west, east, south, north);
    """)
    connection.executemany("INSERT INTO signal_points VALUES (?, ?, ?, ?, ?, ?)",
        ((number, p.osm_id, p.longitude, p.latitude, signal_point_kind(p.tags), json.dumps(p.tags, sort_keys=True))
         for number, p in enumerate(points)))
    connection.executemany("INSERT INTO signal_point_bounds VALUES (?, ?, ?, ?, ?)",
        ((number, p.longitude, p.longitude, p.latitude, p.latitude) for number, p in enumerate(points)))


def read_signal_points(connection: sqlite3.Connection, bounds: Bounds, limit: int
                       ) -> list[dict[str, object]]:
    west, south, east, north = bounds
    rows = connection.execute("""
        SELECT p.osm_id, p.longitude, p.latitude, p.kind, p.tags
        FROM signal_point_bounds b JOIN signal_points p ON p.number=b.number
        WHERE b.west <= ? AND b.east >= ? AND b.south <= ? AND b.north >= ?
          AND p.longitude >= ? AND p.longitude <= ? AND p.latitude >= ? AND p.latitude <= ?
        ORDER BY p.number LIMIT ?
    """, (east, west, north, south, west, east, south, north, limit))
    return [{"type": "Feature", "id": identifier,
             "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
             "properties": {"osm_id": identifier, "kind": kind, "tags": json.loads(tags)}}
            for identifier, longitude, latitude, kind, tags in rows]

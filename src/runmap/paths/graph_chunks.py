"""Persist bounded spatial topology batches for request-local graph assembly."""

from collections import Counter
from dataclasses import dataclass
import json
import sqlite3
from typing import TypedDict, cast

from runmap.routing.types import Arc, Bounds, GraphDocument, Node, Segment


CHUNK_SIZE_M = 1000
MAX_CHUNK_SEGMENTS = 256


class ChunkSegment(TypedDict):
    number: int
    bounds: list[float]
    record: Segment


class ChunkArc(TypedDict):
    number: int
    record: Arc


class ChunkNode(TypedDict):
    record: Node
    degree: int


class TopologyChunk(TypedDict):
    segments: list[ChunkSegment]
    arcs: list[ChunkArc]
    nodes: list[ChunkNode]


@dataclass(frozen=True)
class ChunkWindow:
    graph: GraphDocument
    frontier_nodes: tuple[str, ...]


def write_graph_chunks(connection: sqlite3.Connection) -> None:
    """Build spatial batches offline; long segments retain their full envelopes."""
    connection.executescript("""
        CREATE TABLE graph_chunks (number INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        CREATE VIRTUAL TABLE graph_chunk_bounds USING rtree(number, west, east, south, north);
        CREATE TEMP TABLE chunk_node_degree AS
            SELECT id, count(*) AS degree FROM (
                SELECT source AS id FROM segments UNION ALL SELECT target FROM segments
            ) GROUP BY id;
        CREATE UNIQUE INDEX chunk_degree_id ON chunk_node_degree(id);
    """)
    rows = connection.execute("""
        SELECT number, CAST((west + east) / ? AS INTEGER) AS x,
                       CAST((south + north) / ? AS INTEGER) AS y
        FROM segment_bounds ORDER BY x, y, number
    """, (2 * CHUNK_SIZE_M, 2 * CHUNK_SIZE_M))
    numbers: list[int] = []
    cell: tuple[int, int] | None = None
    chunk_number = 0
    for number, x, y in rows:
        if numbers and (cell != (x, y) or len(numbers) == MAX_CHUNK_SEGMENTS):
            _write_chunk(connection, chunk_number, numbers)
            chunk_number += 1
            numbers = []
        cell = x, y
        numbers.append(number)
    if numbers:
        _write_chunk(connection, chunk_number, numbers)
    connection.execute("DROP TABLE chunk_node_degree")


def _write_chunk(connection: sqlite3.Connection, number: int, numbers: list[int]) -> None:
    placeholders = ",".join("?" for _ in numbers)
    prefix = f"WITH selected AS (SELECT * FROM segments WHERE number IN ({placeholders})) "
    segments: list[ChunkSegment] = [
        dict(number=row[0], bounds=list(row[1:5]), record=json.loads(row[5]))
        for row in connection.execute(prefix + """
            SELECT s.number, b.west, b.south, b.east, b.north, s.payload
            FROM selected s JOIN segment_bounds b ON b.number = s.number ORDER BY s.number
        """, numbers)]
    nodes: list[ChunkNode] = [dict(record=json.loads(row[0]), degree=row[1])
        for row in connection.execute(prefix + """
            SELECT n.payload, d.degree FROM nodes n JOIN (
                SELECT source AS id FROM selected UNION SELECT target FROM selected
            ) e ON e.id = n.id JOIN chunk_node_degree d ON d.id = n.id ORDER BY n.id
        """, numbers)]
    arcs: list[ChunkArc] = [dict(number=row[0], record=json.loads(row[1]))
        for row in connection.execute(prefix + """
            SELECT a.number, a.payload FROM arcs a JOIN selected s ON s.id = a.segment_id
            ORDER BY a.number
        """, numbers)]
    chunk = TopologyChunk(segments=segments, nodes=nodes, arcs=arcs)
    payload = json.dumps(chunk, separators=(",", ":"), allow_nan=False)
    connection.execute("INSERT INTO graph_chunks VALUES (?, ?)", (number, payload))
    bounds = (min(s["bounds"][0] for s in segments), max(s["bounds"][2] for s in segments),
              min(s["bounds"][1] for s in segments), max(s["bounds"][3] for s in segments))
    connection.execute("INSERT INTO graph_chunk_bounds VALUES (?, ?, ?, ?, ?)", (number, *bounds))


def query_graph_chunks(connection: sqlite3.Connection, bounds: Bounds) -> ChunkWindow:
    """Decode candidate chunks one at a time, keeping only intersecting segments."""
    west, south, east, north = bounds
    rows = connection.execute("""
        SELECT c.payload FROM graph_chunk_bounds b JOIN graph_chunks c ON c.number = b.number
        WHERE b.west <= ? AND b.east >= ? AND b.south <= ? AND b.north >= ?
        ORDER BY c.number
    """, (east, west, north, south))
    segments: dict[int, Segment] = {}
    arcs: dict[int, Arc] = {}
    nodes: dict[str, Node] = {}
    degrees: dict[str, int] = {}
    loaded_degree: Counter[str] = Counter()
    for (payload,) in rows:
        chunk = cast(TopologyChunk, json.loads(payload))
        selected: set[str] = set()
        endpoints: set[str] = set()
        for item in chunk["segments"]:
            left, bottom, right, top = item["bounds"]
            if left > east or right < west or bottom > north or top < south:
                continue
            segment = item["record"]
            segments[item["number"]] = segment
            selected.add(segment["id"])
            for node in (segment["from"], segment["to"]):
                endpoints.add(node)
                loaded_degree[node] += 1
        for arc in chunk["arcs"]:
            if arc["record"]["segment_id"] in selected:
                arcs[arc["number"]] = arc["record"]
        for node in chunk["nodes"]:
            key = node["record"]["id"]
            if key in endpoints:
                nodes[key] = node["record"]
                degrees[key] = node["degree"]
    graph = GraphDocument(nodes=[nodes[key] for key in sorted(nodes)],
                          segments=[segments[key] for key in sorted(segments)],
                          arcs=[arcs[key] for key in sorted(arcs)])
    frontier = tuple(key for key in sorted(nodes) if loaded_degree[key] < degrees[key])
    return ChunkWindow(graph, frontier)

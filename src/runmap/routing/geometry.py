"""Packed projected geometry shared by policies and sparse endpoint overlays."""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np
from rasterio.warp import transform

from runmap.routing.coordinates import PackedLines, pack_segment_coordinates
from runmap.routing.fields import Point
from runmap.routing.integer_graph import IntegerGraph
from runmap.routing.types import Arc, Coordinate, Node, Segment


@dataclass(frozen=True)
class ProjectedGeometry:
    node_ids: tuple[str, ...]
    segment_ids: tuple[str, ...]
    node_points: memoryview
    segments: PackedLines
    wgs84: PackedLines
    distance_scale: float
    extra_node_points: tuple[Point, ...] = ()
    extra_segment_points: tuple[tuple[Point, ...], ...] = ()


def prepare_projected_geometry(nodes: Mapping[str, Node], segments: Mapping[str, Segment],
                               arcs: Iterable[Arc], index: IntegerGraph,
                               base: ProjectedGeometry | None = None) -> ProjectedGeometry:
    """Pack the base once; overlays project only their added coordinates."""
    if base is not None:
        return _overlay_geometry(nodes, segments, arcs, index, base)
    if tuple(nodes) != index.node_ids or tuple(segments) != index.segment_ids:
        raise ValueError("Geometry must use the base integer graph ordering")
    wgs84 = pack_segment_coordinates(segments)
    coordinates = np.empty((len(nodes) + len(wgs84.points), 2), dtype=np.float64)
    if nodes:
        coordinates[:len(nodes)] = [(node["longitude"], node["latitude"]) for node in nodes.values()]
    coordinates[len(nodes):] = wgs84.points
    x, y = transform("EPSG:4326", "EPSG:3067", coordinates[:, 0], coordinates[:, 1]) if len(coordinates) else ([], [])
    points = np.column_stack((x, y))
    node_points, segment_points = points[:len(nodes)].copy(), points[len(nodes):].copy()
    node_points.flags.writeable = segment_points.flags.writeable = False
    projected = PackedLines(memoryview(segment_points), wgs84.offsets)
    data = ProjectedGeometry(index.node_ids, index.segment_ids, memoryview(node_points), projected, wgs84, 1.0)
    return _with_distance_scale(data, arcs, index)


def _overlay_geometry(nodes: Mapping[str, Node], segments: Mapping[str, Segment],
                      arcs: Iterable[Arc], index: IntegerGraph, base: ProjectedGeometry) -> ProjectedGeometry:
    if index.node_ids is not base.node_ids or index.segment_ids is not base.segment_ids:
        raise ValueError("Overlay geometry must share the base integer graph ordering")
    expected_nodes = index.extra_node_ids[len(base.extra_node_points):]
    expected_segments = index.extra_segment_ids[len(base.extra_segment_points):]
    if tuple(nodes) != expected_nodes or tuple(segments) != expected_segments:
        raise ValueError("Overlay geometry must append the integer graph additions")
    node_points = projected_points([(node["longitude"], node["latitude"]) for node in nodes.values()]) if nodes else []
    segment_points = tuple(tuple(projected_points(segment["geometry"])) for segment in segments.values())
    data = ProjectedGeometry(base.node_ids, base.segment_ids, base.node_points, base.segments, base.wgs84,
                             base.distance_scale, base.extra_node_points + tuple(node_points),
                             base.extra_segment_points + segment_points)
    return _with_distance_scale(data, arcs, index)


def _with_distance_scale(data: ProjectedGeometry, arcs: Iterable[Arc], index: IntegerGraph) -> ProjectedGeometry:
    scale = data.distance_scale
    for arc in arcs:
        distance = math.dist(indexed_node_point(data, index.node_indices[arc["from"]]),
                             indexed_node_point(data, index.node_indices[arc["to"]]))
        if distance:
            scale = min(scale, arc["length_m"] / distance)
    return replace(data, distance_scale=scale)


def indexed_node_point(geometry: ProjectedGeometry, node: int) -> Point:
    if node < 0:
        raise IndexError("Geometry node index out of range")
    if node < len(geometry.node_points):
        return geometry.node_points[node, 0], geometry.node_points[node, 1]
    return geometry.extra_node_points[node - len(geometry.node_points)]


def indexed_segment_points(geometry: ProjectedGeometry, segment: int) -> tuple[Point, ...]:
    if segment < 0:
        raise IndexError("Geometry segment index out of range")
    if segment < len(geometry.segment_ids):
        points, offsets = geometry.segments.points, geometry.segments.offsets
        return tuple((points[i, 0], points[i, 1]) for i in range(offsets[segment], offsets[segment + 1]))
    return geometry.extra_segment_points[segment - len(geometry.segment_ids)]


def projected_points(geometry: Sequence[Coordinate]) -> list[Point]:
    xs, ys = transform("EPSG:4326", "EPSG:3067", *zip(*geometry))
    return list(zip(xs, ys))


def slice_geometry(geometry: Sequence[Coordinate], start: float, end: float) -> list[Coordinate]:
    """Slice using projected chainage, retaining bends between the cut points."""
    points = projected_points(geometry)
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    result: list[Point] = []
    offset = 0.0
    for first, second, length in zip(points, points[1:], lengths):
        if length and offset + length >= start and offset <= end:
            for cut in (max(start, offset), min(end, offset + length)):
                fraction = (cut - offset) / length
                point = (first[0] + fraction * (second[0] - first[0]),
                         first[1] + fraction * (second[1] - first[1]))
                if not result or point != result[-1]:
                    result.append(point)
        offset += length
    if len(result) < 2:
        raise ValueError("Endpoint chainage does not span a valid path portion")
    xs, ys = transform("EPSG:3067", "EPSG:4326", *zip(*result))
    return [[x, y] for x, y in zip(xs, ys)]



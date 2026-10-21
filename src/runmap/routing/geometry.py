"""Projected geometry shared by endpoint splitting and cost preparation."""

import math
from collections import ChainMap
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from rasterio.warp import transform

from runmap.routing.fields import Point
from runmap.routing.types import Arc, Coordinate, Node, Segment


@dataclass(frozen=True)
class ProjectedGeometry:
    nodes: Mapping[str, Point]
    segments: Mapping[str, tuple[Point, ...]]
    distance_scale: float
    node_points: tuple[Point, ...]
    segment_points: tuple[tuple[Point, ...], ...]
    extra_node_points: tuple[Point, ...] = ()
    extra_segment_points: tuple[tuple[Point, ...], ...] = ()


def prepare_projected_geometry(nodes: Mapping[str, Node], segments: Mapping[str, Segment],
                               arcs: Iterable[Arc],
                               base: ProjectedGeometry | None = None) -> ProjectedGeometry:
    """Batch graph projection once; overlays add only their new geometry."""
    coordinates: list[Coordinate] = [[node["longitude"], node["latitude"]] for node in nodes.values()]
    coordinates.extend(point for segment in segments.values() for point in segment["geometry"])
    points = projected_points(coordinates) if coordinates else []
    node_points = dict(zip(nodes, points[:len(nodes)]))
    segment_points: dict[str, tuple[Point, ...]] = {}
    offset = len(nodes)
    for segment_id, segment in segments.items():
        end = offset + len(segment["geometry"])
        segment_points[segment_id] = tuple(points[offset:end])
        offset = end
    all_nodes = ChainMap(node_points, base.nodes) if base else node_points
    all_segments = ChainMap(segment_points, base.segments) if base else segment_points
    scale = base.distance_scale if base else 1.0
    for arc in arcs:
        distance = math.dist(all_nodes[arc["from"]], all_nodes[arc["to"]])
        if distance:
            scale = min(scale, arc["length_m"] / distance)
    if base is not None:
        return ProjectedGeometry(all_nodes, all_segments, scale, base.node_points, base.segment_points,
                                  base.extra_node_points + tuple(node_points.values()),
                                  base.extra_segment_points + tuple(segment_points.values()))
    return ProjectedGeometry(all_nodes, all_segments, scale,
                              tuple(node_points.values()), tuple(segment_points.values()))


def indexed_node_point(geometry: ProjectedGeometry, node: int) -> Point:
    if node < len(geometry.node_points):
        return geometry.node_points[node]
    return geometry.extra_node_points[node - len(geometry.node_points)]


def indexed_segment_points(geometry: ProjectedGeometry, segment: int) -> tuple[Point, ...]:
    if segment < len(geometry.segment_points):
        return geometry.segment_points[segment]
    return geometry.extra_segment_points[segment - len(geometry.segment_points)]


def projected_points(geometry: list[Coordinate]) -> list[Point]:
    xs, ys = transform("EPSG:4326", "EPSG:3067", *zip(*geometry))
    return list(zip(xs, ys))


def slice_geometry(geometry: list[Coordinate], start: float, end: float) -> list[Coordinate]:
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



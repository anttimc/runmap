"""Projected geometry shared by endpoint splitting and cost preparation."""

import math

from rasterio.warp import transform

from runmap.routing.fields import Point
from runmap.routing.types import Coordinate


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



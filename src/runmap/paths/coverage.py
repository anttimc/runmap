"""Describe source-network coverage independently of request-sized graph windows."""

from collections.abc import Iterable, Sequence
import math
from typing import Literal, TypedDict, cast

from rasterio.warp import transform

from runmap.routing.types import Bounds, Coordinate, GraphDocument


COVERAGE_VERSION = "coverage-band-100m-v1"


class CoverageGeometry(TypedDict):
    type: Literal["Polygon"]
    coordinates: list[list[Coordinate]]


class Coverage(TypedDict):
    geometry: CoverageGeometry
    kind: Literal["prepared_extract", "geometry_envelope"]


def geometry_bounds(coordinates: Iterable[Coordinate]) -> Bounds:
    points = iter(coordinates)
    try:
        west, south = next(points)
    except StopIteration:
        raise ValueError("Geometry has no coordinates") from None
    east, north = west, south
    for x, y in points:
        west, south = min(west, x), min(south, y)
        east, north = max(east, x), max(north, y)
    return west, south, east, north


def point_in_ring(point: Coordinate, ring: Sequence[Coordinate]) -> bool:
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:]):
        cross = (x - a[0]) * (b[1] - a[1]) - (y - a[1]) * (b[0] - a[0])
        if abs(cross) < 1e-12 and min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1]):
            return True
        if (a[1] > y) != (b[1] > y):
            intersection = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < intersection:
                inside = not inside
    return inside


def graph_coverage(graph: GraphDocument) -> Coverage:
    experiment = cast(dict[str, object], graph.get("experiment", {}))
    bounds = cast(Bounds | None, experiment.get("bounds_epsg3067"))
    if bounds:
        west, south, east, north = bounds
        lon, lat = transform("EPSG:3067", "EPSG:4326",
                             [west, east, east, west, west], [south, south, north, north, south])
        ring: list[Coordinate] = [list(point) for point in zip(lon, lat)]
        return {"geometry": {"type": "Polygon", "coordinates": [ring]}, "kind": "prepared_extract"}
    if not graph["segments"]:
        raise ValueError("Cannot determine coverage of an empty network")
    west, south, east, north = geometry_bounds(
        point for segment in graph["segments"] for point in segment["geometry"])
    return {"geometry": {"type": "Polygon", "coordinates": [[
        [west, south], [east, south], [east, north], [west, north], [west, south],
    ]]}, "kind": "geometry_envelope"}


def near_coverage_boundary(coverage: Coverage, geometry: Sequence[Coordinate]) -> bool:
    """Protect the conservative 100 m band where source connections may be missing."""
    ring = coverage["geometry"]["coordinates"][0]
    for x, y in geometry:
        if not point_in_ring((x, y), ring):
            return True
        cos_lat = math.cos(math.radians(y))
        for a, b in zip(ring, ring[1:]):
            ax, ay = (a[0] - x) * cos_lat * 111320, (a[1] - y) * 111320
            bx, by = (b[0] - x) * cos_lat * 111320, (b[1] - y) * 111320
            dx, dy = bx - ax, by - ay
            fraction = max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0
            if math.hypot(ax + fraction * dx, ay + fraction * dy) <= 100:
                return True
    return False

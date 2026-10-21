"""Retain display metadata without duplicating every measurement as GeoJSON."""

from dataclasses import dataclass
from collections.abc import Sequence
from typing import Literal, TypedDict

from runmap.routing.types import Coordinate
from runmap.paths.measurements import MeasurementData, indexed_measurement


Quality = Literal["missing", "uncertain", "terrain_estimate"]


@dataclass(frozen=True, slots=True)
class DisplayInterval:
    segment: int
    interval_index: int
    surface_class: str
    flat_min_grade_50m: float | None


class DisplayProperties(TypedDict):
    segment_id: str
    interval_index: int
    grade_percent: float | None
    quality: Quality
    quality_flags: list[str]
    surface_class: str
    stairs: bool
    flat_min_grade_50m: float | None


class DisplayGeometry(TypedDict):
    type: Literal["LineString"]
    coordinates: list[Coordinate]


class DisplayFeature(TypedDict):
    type: Literal["Feature"]
    id: str
    geometry: DisplayGeometry
    properties: DisplayProperties


def measurement_quality(valid: bool, flags: Sequence[str]) -> Quality:
    if not valid:
        return "missing"
    return "uncertain" if "bridge" in flags or "tunnel" in flags else "terrain_estimate"


def display_feature(data: MeasurementData, interval: DisplayInterval) -> DisplayFeature:
    item = indexed_measurement(data, interval.segment, interval.interval_index)
    return {
        "type": "Feature", "id": f"{item['segment_id']}:{item['interval_index']}",
        "geometry": {"type": "LineString", "coordinates": item["geometry"]},
        "properties": {
            "segment_id": item["segment_id"], "interval_index": item["interval_index"],
            "grade_percent": item["grade_percent"], "quality": measurement_quality(item["valid"], item["quality_flags"]),
            "quality_flags": item["quality_flags"], "surface_class": interval.surface_class,
            "stairs": "stairs" in item["quality_flags"],
            "flat_min_grade_50m": interval.flat_min_grade_50m,
        },
    }

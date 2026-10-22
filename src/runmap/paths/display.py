"""Retain display metadata without duplicating every measurement as GeoJSON."""

from array import array
from bisect import bisect_right
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Literal, TypedDict, overload

from runmap.routing.types import Coordinate, Measurement
from runmap.paths.measurements import MeasurementData, indexed_measurement


Quality = Literal["missing", "uncertain", "terrain_estimate"]


@dataclass(frozen=True, slots=True)
class DisplayInterval:
    segment: int
    interval_index: int
    surface_class: str
    flat_min_grade_50m: float | None


@dataclass(frozen=True)
class DisplayIntervals(Sequence[DisplayInterval]):
    """Read-only interval views using the measurement store's segment ordering."""

    offsets: memoryview
    surfaces: tuple[str, ...]
    flat_thresholds: memoryview
    present: memoryview

    def __len__(self) -> int:
        return len(self.flat_thresholds)

    @overload
    def __getitem__(self, index: int) -> DisplayInterval: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[DisplayInterval, ...]: ...

    def __getitem__(self, index: int | slice) -> DisplayInterval | tuple[DisplayInterval, ...]:
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(len(self))))
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        segment = bisect_right(self.offsets, index) - 1
        threshold = self.flat_thresholds[index] if self.present[index] else None
        return DisplayInterval(segment, index - self.offsets[segment], self.surfaces[segment], threshold)


def pack_display_intervals(data: MeasurementData, intervals: Sequence[DisplayInterval]) -> DisplayIntervals:
    """Retain only thresholds and per-segment surfaces alongside shared offsets."""
    offsets = memoryview(data.interval_offsets)
    if len(intervals) != offsets[-1]:
        raise ValueError("Display/measurement interval count mismatch")
    surfaces = [""] * len(data.segment_ids)
    thresholds = array("d")
    present = bytearray()
    for segment in range(len(surfaces)):
        for number in range(offsets[segment], offsets[segment + 1]):
            item = intervals[number]
            if item.segment != segment or item.interval_index != number - offsets[segment]:
                raise ValueError("Display/measurement interval ordering mismatch")
            if number == offsets[segment]:
                surfaces[segment] = item.surface_class
            elif surfaces[segment] != item.surface_class:
                raise ValueError("Display surface must be constant within a segment")
            thresholds.append(0.0 if item.flat_min_grade_50m is None else item.flat_min_grade_50m)
            present.append(item.flat_min_grade_50m is not None)
    return DisplayIntervals(offsets, tuple(surfaces), memoryview(thresholds.tobytes()).cast("d"),
                            memoryview(bytes(present)))


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
    return measurement_feature(item, interval.surface_class, interval.flat_min_grade_50m)


def measurement_feature(item: Measurement, surface_class: str, flat_min_grade_50m: float | None) -> DisplayFeature:
    return {
        "type": "Feature", "id": f"{item['segment_id']}:{item['interval_index']}",
        "geometry": {"type": "LineString", "coordinates": item["geometry"]},
        "properties": {
            "segment_id": item["segment_id"], "interval_index": item["interval_index"],
            "grade_percent": item["grade_percent"], "quality": measurement_quality(item["valid"], item["quality_flags"]),
            "quality_flags": item["quality_flags"], "surface_class": surface_class,
            "stairs": "stairs" in item["quality_flags"],
            "flat_min_grade_50m": flat_min_grade_50m,
        },
    }

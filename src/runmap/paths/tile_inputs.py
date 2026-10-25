"""Compact immutable gradient samples shared across static path tiles."""

from array import array
from collections.abc import Iterable
from dataclasses import dataclass
import math

from runmap.paths.display import measurement_quality
from runmap.paths.preparation import PreparedDisplayInterval


QUALITIES = ("missing", "uncertain", "terrain_estimate")


@dataclass(frozen=True)
class TileSegment:
    id: str
    eligible: bool


@dataclass(frozen=True)
class TileInputs:
    segments: tuple[TileSegment, ...]
    indices: memoryview
    values: memoryview
    flags: memoryview
    geometry_offsets: memoryview
    geometry: memoryview


def pack_tile_inputs(intervals: Iterable[PreparedDisplayInterval]) -> TileInputs:
    segments: list[TileSegment] = []
    lookup: dict[str, int] = {}
    indices, offsets = array("q"), array("q", [0])
    values, geometry = array("d"), array("d")
    flags = bytearray()
    for stored in intervals:
        item = stored.measurement
        key = item["segment_id"]
        if key not in lookup:
            if stored.policy is None:
                raise ValueError("Prepared policy data is missing")
            lookup[key] = len(segments)
            segments.append(TileSegment(key, stored.policy.reason is None))
        indices.extend((lookup[key], item["interval_index"]))
        values.extend((item["grade_percent"] if item["grade_percent"] is not None else math.nan,
                       stored.flat_min_grade_50m if stored.flat_min_grade_50m is not None else math.nan))
        quality = measurement_quality(item["valid"], item["quality_flags"])
        flags.extend((QUALITIES.index(quality), int("stairs" in item["quality_flags"])))
        for point in item["geometry"]:
            geometry.extend((point[0], point[1]))
        offsets.append(len(geometry) // 2)
    return TileInputs(tuple(segments), memoryview(indices.tobytes()).cast("q"),
        memoryview(values.tobytes()).cast("d"), memoryview(bytes(flags)),
        memoryview(offsets.tobytes()).cast("q"), memoryview(geometry.tobytes()).cast("d"))

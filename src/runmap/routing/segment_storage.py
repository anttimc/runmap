"""Packed physical-segment metadata sharing IDs and coordinate buffers."""

from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, overload

from runmap.routing.coordinates import PackedLine, PackedLines
from runmap.routing.metadata_strings import StringRows, pack_string_rows, string_row
from runmap.routing.types import Segment


Metric = Literal["length_m", "ascent_m", "descent_m", "terrain_valid_fraction"]
METRICS: tuple[Metric, ...] = ("length_m", "ascent_m", "descent_m", "terrain_valid_fraction")
Boolean = Literal["forward", "backward", "terrain_totals_complete"]
BOOLEANS: tuple[Boolean, ...] = ("forward", "backward", "terrain_totals_complete")
# Presence bits: way/tags 0..1, booleans 2..4, numeric optionals 5..7, sample count 8.
OPTIONAL = ("osm_way_id", "tags", *BOOLEANS, *METRICS[1:], "terrain_sample_count")
SEGMENT_FIELDS = frozenset(("id", "from", "to", "geometry", "length_m", *OPTIONAL))


@dataclass(frozen=True, slots=True)
class SegmentColumns:
    ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    sources: memoryview
    targets: memoryview
    way_ids: tuple[str | None, ...]
    metrics: memoryview
    integers: memoryview
    counts: memoryview
    present: memoryview
    booleans: memoryview
    tags: StringRows
    geometry: PackedLines


@dataclass(frozen=True, slots=True, eq=False)
class SegmentRecords(Sequence[Segment]):
    data: SegmentColumns

    def __len__(self) -> int:
        return len(self.data.ids)

    @overload
    def __getitem__(self, index: int) -> Segment: ...

    @overload
    def __getitem__(self, index: slice) -> list[Segment]: ...

    def __getitem__(self, index: int | slice) -> Segment | list[Segment]:
        if isinstance(index, slice):
            return [external_segment(self.data, i) for i in range(*index.indices(len(self)))]
        return external_segment(self.data, index + len(self) if index < 0 else index)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return NotImplemented
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))

    def __deepcopy__(self, memo: dict[int, object]) -> list[Segment]:
        result = list(self)
        memo[id(self)] = result
        return result


def external_segment(data: SegmentColumns, number: int) -> Segment:
    if not 0 <= number < len(data.ids):
        raise IndexError("Segment index out of range")
    record: Segment = {"id": data.ids[number], "from": data.node_ids[data.sources[number]],
                       "to": data.node_ids[data.targets[number]], "geometry": PackedLine(data.geometry, number),
                       "length_m": data.metrics[4 * number]}
    present, integers = data.present[number], data.integers[number]
    for column, key in enumerate(METRICS):
        if column == 0 or present & (1 << (column + 4)):
            value = data.metrics[4 * number + column]
            record[key] = int(value) if integers & (1 << column) else value
    for column, key in enumerate(BOOLEANS):
        if present & (1 << (column + 2)):
            record[key] = bool(data.booleans[number] & (1 << column))
    if present & 1:
        way = data.way_ids[number]
        assert way is not None
        record["osm_way_id"] = way
    if present & 2:
        record["tags"] = string_row(data.tags, number)
    if present & (1 << 8):
        record["terrain_sample_count"] = data.counts[number]
    return record


def _segment_flags(segment: Segment) -> tuple[int, int, int]:
    if any(key in segment and type(segment[key]) is not bool for key in BOOLEANS):
        raise ValueError("Segment direction and completeness flags must be booleans")
    present = sum((1 << column) for column, key in enumerate(OPTIONAL) if key in segment)
    integers = sum((1 << column) for column, key in enumerate(METRICS)
                   if key in segment and type(segment[key]) is int)
    booleans = sum((1 << column) for column, key in enumerate(BOOLEANS) if segment.get(key, False))
    return present, integers, booleans


def pack_segments(segments: Mapping[str, Segment], ids: tuple[str, ...], node_ids: tuple[str, ...],
                  node_indices: Mapping[str, int], geometry: PackedLines) -> SegmentColumns:
    if tuple(segments) != ids or len(geometry.offsets) != len(ids) + 1:
        raise ValueError("Segments must use the base integer graph ordering")
    sources, targets, counts = array("q"), array("q"), array("q")
    metrics, present = array("d"), array("H")
    integers, booleans = bytearray(), bytearray()
    ways: list[str | None] = []
    way_pool: dict[str, str] = {}
    for segment in segments.values():
        if segment.keys() - SEGMENT_FIELDS:
            raise ValueError("Unsupported segment metadata fields")
        sources.append(node_indices[segment["from"]])
        targets.append(node_indices[segment["to"]])
        way = segment.get("osm_way_id")
        ways.append(way_pool.setdefault(way, way) if way is not None else None)
        metrics.extend(segment.get(key, 0.0) for key in METRICS)
        counts.append(segment.get("terrain_sample_count", 0))
        presence, integer, boolean = _segment_flags(segment)
        present.append(presence)
        integers.append(integer)
        booleans.append(boolean)
    tags = pack_string_rows(segment.get("tags", {}) for segment in segments.values())
    return SegmentColumns(ids, node_ids, memoryview(sources.tobytes()).cast("q"),
                          memoryview(targets.tobytes()).cast("q"), tuple(ways),
                          memoryview(metrics.tobytes()).cast("d"), memoryview(bytes(integers)),
                          memoryview(counts.tobytes()).cast("q"), memoryview(present.tobytes()).cast("H"),
                          memoryview(bytes(booleans)), tags, geometry)

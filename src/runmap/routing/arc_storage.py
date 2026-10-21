"""Packed arc columns and on-demand external record views."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, overload

import numpy as np

from runmap.routing.profiles import arc_forward
from runmap.routing.types import Arc, Segment

ArcMetric = Literal["ascent_m", "descent_m", "terrain_valid_fraction"]
OPTIONAL_COLUMNS: tuple[ArcMetric, ...] = ("ascent_m", "descent_m", "terrain_valid_fraction")
ARC_KEYS = frozenset(("id", "from", "to", "segment_id", "length_m", *OPTIONAL_COLUMNS))


@dataclass(frozen=True, slots=True)
class ArcColumns:
    ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    segment_ids: tuple[str, ...]
    sources: memoryview
    targets: memoryview
    segments: memoryview
    lengths: memoryview
    reverse: memoryview
    optional: memoryview
    flags: memoryview  # optional presence bits 0..2, integer spelling bits 3..6


@dataclass(frozen=True, slots=True, eq=False)
class ArcRecords(Sequence[Arc]):
    data: ArcColumns

    def __len__(self) -> int:
        return len(self.data.ids)

    @overload
    def __getitem__(self, index: int) -> Arc: ...

    @overload
    def __getitem__(self, index: slice) -> list[Arc]: ...

    def __getitem__(self, index: int | slice) -> Arc | list[Arc]:
        if isinstance(index, slice):
            return [external_arc_record(self.data, i) for i in range(*index.indices(len(self)))]
        index = index + len(self) if index < 0 else index
        if not 0 <= index < len(self):
            raise IndexError("Arc index out of range")
        return external_arc_record(self.data, index)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return NotImplemented
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))

    def __deepcopy__(self, memo: dict[int, object]) -> list[Arc]:
        # Copies of graph documents remain editable preparation documents.
        result = list(self)
        memo[id(self)] = result
        return result


@dataclass(frozen=True, slots=True, eq=False)
class ArcLookup(Mapping[str, Arc]):
    data: ArcColumns
    indices: Mapping[str, int]

    def __len__(self) -> int:
        return len(self.data.ids)

    def __iter__(self) -> Iterator[str]:
        return iter(self.data.ids)

    def __getitem__(self, key: str) -> Arc:
        return external_arc_record(self.data, self.indices[key])


def external_arc_record(data: ArcColumns, number: int) -> Arc:
    if not 0 <= number < len(data.ids):
        raise IndexError("Arc index out of range")
    flags = data.flags[number]
    length = data.lengths[number]
    record: Arc = {"id": data.ids[number], "segment_id": data.segment_ids[data.segments[number]],
                   "from": data.node_ids[data.sources[number]], "to": data.node_ids[data.targets[number]],
                   "length_m": int(length) if flags & 8 else length}
    for column, key in enumerate(OPTIONAL_COLUMNS):
        if flags & (1 << column):
            value = data.optional[number, column]
            record[key] = int(value) if flags & (1 << (column + 4)) else value
    return record


def pack_arcs(arcs: Mapping[str, Arc], node_ids: tuple[str, ...], nodes: Mapping[str, int],
              segment_ids: tuple[str, ...], segments: Mapping[str, int],
              physical: Mapping[str, Segment]) -> ArcColumns:
    count = len(arcs)
    sources = np.empty(count, dtype=np.int64)
    targets = np.empty(count, dtype=np.int64)
    physical_segments = np.empty(count, dtype=np.int64)
    lengths = np.empty(count, dtype=np.float64)
    reverse = np.empty(count, dtype=np.bool_)
    optional = np.zeros((count, 3), dtype=np.float64)
    flags = np.zeros(count, dtype=np.uint8)
    for number, arc in enumerate(arcs.values()):
        if arc.keys() - ARC_KEYS:
            raise ValueError("Unsupported arc metadata fields")
        sources[number] = nodes[arc["from"]]
        targets[number] = nodes[arc["to"]]
        physical_segments[number] = segments[arc["segment_id"]]
        lengths[number] = arc["length_m"]
        reverse[number] = not arc_forward(physical[arc["segment_id"]], arc)
        flags[number] = 8 if type(arc["length_m"]) is int else 0
        for column, key in enumerate(OPTIONAL_COLUMNS):
            if key in arc:
                optional[number, column] = arc[key]
                flags[number] |= (1 << column) | ((1 << (column + 4)) if type(arc[key]) is int else 0)
    for values in (sources, targets, physical_segments, lengths, reverse, optional, flags):
        values.flags.writeable = False
    return ArcColumns(tuple(arcs), node_ids, segment_ids, memoryview(sources), memoryview(targets), memoryview(physical_segments), memoryview(lengths),
                      memoryview(reverse), memoryview(optional), memoryview(flags))

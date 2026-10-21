"""Packed coordinates with small read-only views for segment metadata."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import overload

import numpy as np

from runmap.routing.types import Coordinate, Segment


@dataclass(frozen=True, slots=True)
class PackedLines:
    points: memoryview
    offsets: memoryview
    integers: memoryview | None = None


@dataclass(frozen=True, slots=True, eq=False)
class PackedLine(Sequence[Coordinate]):
    """Expose one packed line without retaining Python coordinate objects."""

    data: PackedLines
    segment: int

    def __len__(self) -> int:
        return self.data.offsets[self.segment + 1] - self.data.offsets[self.segment]

    @overload
    def __getitem__(self, index: int) -> Coordinate: ...

    @overload
    def __getitem__(self, index: slice) -> list[Coordinate]: ...

    def __getitem__(self, index: int | slice) -> Coordinate | list[Coordinate]:
        count = len(self)
        start = self.data.offsets[self.segment]
        if isinstance(index, slice):
            return [_coordinate(self.data, start + i) for i in range(*index.indices(count))]
        index = index + count if index < 0 else index
        if not 0 <= index < count:
            raise IndexError("Coordinate index out of range")
        return _coordinate(self.data, start + index)

    def __iter__(self) -> Iterator[Coordinate]:
        start, end = self.data.offsets[self.segment], self.data.offsets[self.segment + 1]
        return (_coordinate(self.data, i) for i in range(start, end))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return NotImplemented
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))

    def __deepcopy__(self, memo: dict[int, object]) -> "PackedLine":
        # The backing store is shared and read-only, including across metadata copies.
        memo[id(self)] = self
        return self


def _coordinate(data: PackedLines, number: int) -> Coordinate:
    x, y = data.points[number, 0], data.points[number, 1]
    if data.integers is not None:
        x = int(x) if data.integers[number, 0] else x
        y = int(y) if data.integers[number, 1] else y
    return [x, y]


def pack_segment_coordinates(segments: Mapping[str, Segment]) -> PackedLines:
    """Replace coordinate lists while preserving exact published JSON numbers."""
    offsets = np.zeros(len(segments) + 1, dtype=np.int64)
    for i, segment in enumerate(segments.values()):
        offsets[i + 1] = offsets[i] + len(segment["geometry"])
    points = np.empty((int(offsets[-1]), 2), dtype=np.float64)
    integers = None
    for i, segment in enumerate(segments.values()):
        start, end = int(offsets[i]), int(offsets[i + 1])
        if start != end:
            points[start:end] = segment["geometry"]
        for number, point in enumerate(segment["geometry"], start):
            for axis, value in enumerate(point):
                if type(value) is int:
                    if integers is None:
                        integers = np.zeros(points.shape, dtype=np.bool_)
                    integers[number, axis] = True
    for array in (points, offsets, integers):
        if array is not None:
            array.flags.writeable = False
    data = PackedLines(memoryview(points), memoryview(offsets), None if integers is None else memoryview(integers))
    for i, segment in enumerate(segments.values()):
        segment["geometry"] = PackedLine(data, i)
    return data

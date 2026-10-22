"""Compact immutable interval references for spatial candidate queries."""

from array import array
from collections.abc import Mapping, Sequence, Set


Cell = tuple[int, int]


def pack_cells(cells: Mapping[Cell, Sequence[int]]) -> dict[Cell, memoryview]:
    """Release Python integer/list ownership after spatial preparation."""
    return {cell: memoryview(array("q", indexes).tobytes()).cast("q")
            for cell, indexes in cells.items()}


def filter_cells(cells: Mapping[Cell, Sequence[int]], visible: Set[int]) -> dict[Cell, memoryview]:
    """Share unchanged immutable rows between display policies."""
    result: dict[Cell, memoryview] = {}
    for cell, indexes in cells.items():
        selected = array("q", (index for index in indexes if index in visible))
        if len(selected) == len(indexes) and isinstance(indexes, memoryview) and indexes.readonly:
            result[cell] = indexes
        else:
            result[cell] = memoryview(selected.tobytes()).cast("q")
    return result

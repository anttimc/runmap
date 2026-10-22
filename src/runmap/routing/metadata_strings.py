"""Pooled strings with ordered, detached metadata rows."""

from array import array
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StringRows:
    offsets: memoryview
    keys: memoryview
    values: memoryview
    strings: tuple[str, ...]


def pack_string_rows(rows: Iterable[Mapping[str, str]]) -> StringRows:
    offsets, keys, values = array("q", [0]), array("I"), array("I")
    pool: dict[str, int] = {}
    for row in rows:
        for key, value in row.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("Metadata keys and values must be strings")
            keys.append(pool.setdefault(key, len(pool)))
            values.append(pool.setdefault(value, len(pool)))
        offsets.append(len(keys))
    return StringRows(memoryview(offsets.tobytes()).cast("q"), memoryview(keys.tobytes()).cast("I"),
                      memoryview(values.tobytes()).cast("I"), tuple(pool))


def string_row(data: StringRows, number: int) -> dict[str, str]:
    if not 0 <= number < len(data.offsets) - 1:
        raise IndexError("Metadata row out of range")
    return {data.strings[data.keys[i]]: data.strings[data.values[i]]
            for i in range(data.offsets[number], data.offsets[number + 1])}

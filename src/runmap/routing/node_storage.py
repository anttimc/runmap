"""Packed node coordinates and on-demand public records."""

from array import array
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar, cast, overload

from runmap.routing.metadata_strings import StringRows, pack_string_rows, string_row
from runmap.routing.types import Node


Record = TypeVar("Record", covariant=True)
NODE_FIELDS = frozenset(("id", "longitude", "latitude", "barrier", "access", "foot",
                         "entrance", "crossing", "highway", "tags"))


@dataclass(frozen=True, slots=True)
class NodeColumns:
    ids: tuple[str, ...]
    coordinates: memoryview
    integers: memoryview
    metadata: StringRows
    tags: StringRows | None


@dataclass(frozen=True, slots=True, eq=False)
class MetadataLookup(Mapping[str, Record], Generic[Record]):
    """Reuse the integer graph's ID map for lazy external metadata access."""

    records: Sequence[Record]
    indices: Mapping[str, int]

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[str]:
        return iter(self.indices)

    def __getitem__(self, key: str) -> Record:
        return self.records[self.indices[key]]


@dataclass(frozen=True, slots=True, eq=False)
class NodeRecords(Sequence[Node]):
    data: NodeColumns

    def __len__(self) -> int:
        return len(self.data.ids)

    @overload
    def __getitem__(self, index: int) -> Node: ...

    @overload
    def __getitem__(self, index: slice) -> list[Node]: ...

    def __getitem__(self, index: int | slice) -> Node | list[Node]:
        if isinstance(index, slice):
            return [external_node(self.data, i) for i in range(*index.indices(len(self)))]
        return external_node(self.data, index + len(self) if index < 0 else index)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return NotImplemented
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))

    def __deepcopy__(self, memo: dict[int, object]) -> list[Node]:
        result = list(self)
        memo[id(self)] = result
        return result


def external_node(data: NodeColumns, number: int) -> Node:
    if not 0 <= number < len(data.ids):
        raise IndexError("Node index out of range")
    longitude, latitude = data.coordinates[2 * number], data.coordinates[2 * number + 1]
    flags = data.integers[number]
    record: Node = {"id": data.ids[number],
                    "longitude": int(longitude) if flags & 1 else longitude,
                    "latitude": int(latitude) if flags & 2 else latitude}
    record.update(cast(Node, string_row(data.metadata, number)))
    if flags & 4:
        assert data.tags is not None
        record["tags"] = string_row(data.tags, number)
    return record


def pack_nodes(nodes: Mapping[str, Node], ids: tuple[str, ...]) -> NodeColumns:
    if tuple(nodes) != ids:
        raise ValueError("Nodes must use the base integer graph ordering")
    coordinates, integers = array("d"), bytearray()
    for node in nodes.values():
        if node.keys() - NODE_FIELDS:
            raise ValueError("Unsupported node metadata fields")
        coordinates.extend((node["longitude"], node["latitude"]))
        integers.append((type(node["longitude"]) is int) | ((type(node["latitude"]) is int) << 1)
                        | (("tags" in node) << 2))
    metadata = pack_string_rows(
        {key: value for key, value in node.items() if key not in {"id", "longitude", "latitude", "tags"}}
        for node in nodes.values())
    return NodeColumns(ids, memoryview(coordinates.tobytes()).cast("d"), memoryview(bytes(integers)), metadata,
                       (pack_string_rows(node.get("tags", {}) for node in nodes.values())
                        if any("tags" in node for node in nodes.values()) else None))

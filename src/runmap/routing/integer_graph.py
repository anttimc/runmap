"""Shared packed arc topology, policy masks, and sparse request overlays."""

from collections import ChainMap
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from runmap.routing.arc_storage import ArcColumns, external_arc_record, pack_arcs
from runmap.routing.types import Arc, Node, Segment
from runmap.routing.profiles import arc_forward


class IntegerArc(NamedTuple):
    index: int
    source: int
    target: int
    segment: int
    length_m: float
    reverse: bool


@dataclass(frozen=True, slots=True)
class PackedAdjacency:
    offsets: memoryview
    arcs: memoryview
    node_order: memoryview


@dataclass(frozen=True, slots=True)
class IntegerGraph:
    node_ids: tuple[str, ...]
    node_indices: Mapping[str, int]
    segment_ids: tuple[str, ...]
    segment_indices: Mapping[str, int]
    arc_indices: Mapping[str, int]
    arc_ids: tuple[str, ...]
    arcs: ArcColumns
    outgoing: PackedAdjacency
    incoming: PackedAdjacency
    enabled: memoryview
    extra_node_ids: tuple[str, ...] = ()
    extra_segment_ids: tuple[str, ...] = ()
    extra_arcs: tuple[IntegerArc, ...] = ()
    extra_arc_ids: tuple[str, ...] = ()
    extra_records: Mapping[int, Arc] = field(default_factory=dict)
    arc_overrides: Mapping[int, IntegerArc] = field(default_factory=dict)
    outgoing_overrides: Mapping[int, tuple[int, ...]] = field(default_factory=dict)
    incoming_overrides: Mapping[int, tuple[int, ...]] = field(default_factory=dict)


def _integer_arc(index: int, arc: Arc, nodes: Mapping[str, int], segments: Mapping[str, int],
                 physical: Segment) -> IntegerArc:
    return IntegerArc(index, nodes[arc['from']], nodes[arc['to']], segments[arc['segment_id']],
                      float(arc['length_m']), not arc_forward(physical, arc))


def packed_adjacency(nodes: NDArray[np.int64], arc_numbers: NDArray[np.int64], count: int) -> PackedAdjacency:
    """Keep original arc order within each row, including parallel arcs and loops."""
    offsets = np.zeros(count + 1, dtype=np.int64)
    offsets[1:] = np.cumsum(np.bincount(nodes, minlength=count))
    order = np.argsort(nodes, kind="stable")
    entries = np.asarray(arc_numbers[order], dtype=np.int64)
    _, first = np.unique(nodes, return_index=True)
    node_order = np.asarray(nodes[np.sort(first)], dtype=np.int64)
    for values in (offsets, entries, node_order):
        values.flags.writeable = False
    return PackedAdjacency(memoryview(offsets), memoryview(entries), memoryview(node_order))


def prepare_integer_graph(nodes: Mapping[str, Node], segments: Mapping[str, Segment],
                          arcs: Mapping[str, Arc]) -> IntegerGraph:
    node_ids, segment_ids = tuple(nodes), tuple(segments)
    node_indices = {node: i for i, node in enumerate(node_ids)}
    segment_indices = {segment: i for i, segment in enumerate(segment_ids)}
    arc_indices = {arc: i for i, arc in enumerate(arcs)}
    columns = pack_arcs(arcs, node_ids, node_indices, segment_ids, segment_indices, segments)
    numbers = np.arange(len(columns.ids), dtype=np.int64)
    enabled = np.ones(len(numbers), dtype=np.bool_)
    enabled.flags.writeable = False
    return IntegerGraph(node_ids, node_indices, segment_ids, segment_indices, arc_indices, columns.ids,
                        columns, packed_adjacency(np.asarray(columns.sources), numbers, len(nodes)),
                        packed_adjacency(np.asarray(columns.targets), numbers, len(nodes)), memoryview(enabled))


def apply_integer_policy(base: IntegerGraph, enabled: NDArray[np.bool_]) -> IntegerGraph:
    """Share all topology and retain only the new policy's eligibility mask."""
    if enabled.dtype != np.bool_ or enabled.shape != (len(base.arc_ids),):
        raise ValueError("Arc eligibility mask must match the base graph")
    # Publish detached immutable data; callers cannot mutate a cached policy via their array.
    return replace(base, enabled=memoryview(enabled.tobytes()).cast("?"))


def filtered_adjacency(base: PackedAdjacency, enabled: memoryview) -> PackedAdjacency:
    """Prepare the alternative layout for policy/search tradeoff measurements."""
    offsets = np.asarray(base.offsets)
    entries = np.asarray(base.arcs)
    numbers = entries[np.asarray(enabled)[entries]]
    nodes = np.repeat(np.arange(len(offsets) - 1), np.diff(offsets))
    return packed_adjacency(nodes[np.asarray(enabled)[entries]], numbers, len(offsets) - 1)


def _extend_ids(count: int, extra: tuple[str, ...], indices: Mapping[str, int],
                additions: Mapping[str, object]) -> tuple[tuple[str, ...], Mapping[str, int]]:
    added = tuple(key for key in additions if key not in indices)
    if not added:
        return extra, indices
    return extra + added, ChainMap({key: count + i for i, key in enumerate(added)}, indices)


def integer_arc(graph: IntegerGraph, index: int) -> IntegerArc:
    if index < 0:
        raise IndexError("Arc index out of range")
    overridden = graph.arc_overrides.get(index)
    if overridden is not None:
        return overridden
    if index >= len(graph.arc_ids):
        return graph.extra_arcs[index - len(graph.arc_ids)]
    columns = graph.arcs
    return IntegerArc(index, columns.sources[index], columns.targets[index],
                      columns.segments[index], columns.lengths[index], columns.reverse[index])


def external_arc(graph: IntegerGraph, number: int) -> Arc:
    if number in graph.extra_records:
        return graph.extra_records[number]
    return external_arc_record(graph.arcs, number)


def adjacency_arcs(graph: IntegerGraph, node: int, reverse: bool = False) -> Iterator[int]:
    rows = graph.incoming if reverse else graph.outgoing
    overrides = graph.incoming_overrides if reverse else graph.outgoing_overrides
    updates = overrides.get(node)
    if updates is not None:
        return iter(updates)
    return iter(rows.arcs[rows.offsets[node]:rows.offsets[node + 1]])


def arc_enabled(graph: IntegerGraph, number: int) -> bool:
    return number >= len(graph.arc_ids) or number in graph.arc_overrides or graph.enabled[number]


@dataclass(frozen=True, slots=True, eq=False)
class AdjacencyLookup(Mapping[str, list[str]]):
    graph: IntegerGraph
    reverse: bool = False

    def __iter__(self) -> Iterator[str]:
        rows = self.graph.incoming if self.reverse else self.graph.outgoing
        return (self.graph.node_ids[node] for node in rows.node_order
                if any(arc_enabled(self.graph, arc) for arc in adjacency_arcs(self.graph, node, self.reverse)))

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __getitem__(self, key: str) -> list[str]:
        node = self.graph.node_indices[key]
        result = [integer_arc_id(self.graph, arc) for arc in adjacency_arcs(self.graph, node, self.reverse)
                  if arc_enabled(self.graph, arc)]
        if not result:
            raise KeyError(key)
        return result


def integer_node_id(graph: IntegerGraph, node: int) -> str:
    return graph.node_ids[node] if node < len(graph.node_ids) else graph.extra_node_ids[node - len(graph.node_ids)]


def integer_arc_id(graph: IntegerGraph, arc: int) -> str:
    return graph.arc_ids[arc] if arc < len(graph.arc_ids) else graph.extra_arc_ids[arc - len(graph.arc_ids)]


def integer_segment_id(graph: IntegerGraph, segment: int) -> str:
    if segment < len(graph.segment_ids):
        return graph.segment_ids[segment]
    return graph.extra_segment_ids[segment - len(graph.segment_ids)]


def _overlay_adjacency(graph: IntegerGraph, base: Mapping[int, tuple[int, ...]],
                       updates: Mapping[str, list[str]], new_nodes: Mapping[str, Node]) -> dict[int, tuple[int, ...]]:
    rows = dict(base)
    for node in new_nodes:
        rows.setdefault(graph.node_indices[node], ())
    for node, arc_ids in updates.items():
        rows[graph.node_indices[node]] = tuple(graph.arc_indices[a] for a in arc_ids)
    return rows


def extend_integer_graph(base: IntegerGraph | None, nodes: Mapping[str, Node],
                         segments: Mapping[str, Segment], arcs: Mapping[str, Arc],
                         outgoing: Mapping[str, list[str]], incoming: Mapping[str, list[str]], *,
                         segment_records: Mapping[str, Segment]) -> IntegerGraph:
    """Share packed topology; endpoint splits replace only their adjacency rows."""
    if base is None:
        raise ValueError('Graph has no prepared integer index')
    extra_nodes, node_indices = _extend_ids(len(base.node_ids) + len(base.extra_node_ids),
                                          base.extra_node_ids, base.node_indices, nodes)
    extra_segments, segment_indices = _extend_ids(len(base.segment_ids) + len(base.extra_segment_ids),
                                                base.extra_segment_ids, base.segment_indices, segments)
    records = list(base.extra_arcs)
    extra_arc_ids = list(base.extra_arc_ids)
    overrides = dict(base.arc_overrides)
    external = dict(base.extra_records)
    updates: dict[str, int] = {}
    for arc_id, arc in arcs.items():
        position = base.arc_indices.get(arc_id, len(base.arc_ids) + len(records))
        record = _integer_arc(position, arc, node_indices, segment_indices, segment_records[arc['segment_id']])
        if position == len(base.arc_ids) + len(records):
            records.append(record)
            extra_arc_ids.append(arc_id)
        else:
            overrides[position] = record
        external[position] = arc
        updates[arc_id] = position
    graph = replace(base, node_indices=node_indices, segment_indices=segment_indices,
                    arc_indices=ChainMap(updates, base.arc_indices), extra_node_ids=extra_nodes,
                    extra_segment_ids=extra_segments, extra_arcs=tuple(records),
                    extra_arc_ids=tuple(extra_arc_ids), extra_records=external, arc_overrides=overrides)
    return replace(graph,
                   outgoing_overrides=_overlay_adjacency(graph, base.outgoing_overrides, outgoing, nodes),
                   incoming_overrides=_overlay_adjacency(graph, base.incoming_overrides, incoming, nodes))

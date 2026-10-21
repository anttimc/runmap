"""Dense search indices alongside the graph's stable external identifiers."""

from collections import ChainMap
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from runmap.routing.types import Arc, Node, Segment
from runmap.routing.profiles import arc_forward


@dataclass(frozen=True, slots=True)
class IntegerArc:
    index: int
    source: int
    target: int
    segment: int
    length_m: float
    record: Arc
    reverse: bool


@dataclass(frozen=True, slots=True)
class IntegerGraph:
    node_ids: tuple[str, ...]
    node_indices: Mapping[str, int]
    segment_ids: tuple[str, ...]
    segment_indices: Mapping[str, int]
    arc_indices: Mapping[str, int]
    arc_ids: tuple[str, ...]
    arcs: tuple[IntegerArc, ...]
    outgoing: tuple[tuple[IntegerArc, ...], ...]
    incoming: tuple[tuple[IntegerArc, ...], ...]
    extra_node_ids: tuple[str, ...] = ()
    extra_segment_ids: tuple[str, ...] = ()
    extra_arcs: tuple[IntegerArc, ...] = ()
    extra_arc_ids: tuple[str, ...] = ()
    arc_overrides: Mapping[int, IntegerArc] = field(default_factory=dict)
    outgoing_overrides: Mapping[int, tuple[IntegerArc, ...]] = field(default_factory=dict)
    incoming_overrides: Mapping[int, tuple[IntegerArc, ...]] = field(default_factory=dict)


def _integer_arc(index: int, arc: Arc, nodes: Mapping[str, int], segments: Mapping[str, int],
                 physical: Segment) -> IntegerArc:
    return IntegerArc(index, nodes[arc['from']], nodes[arc['to']], segments[arc['segment_id']],
                      float(arc['length_m']), arc, not arc_forward(physical, arc))


def _adjacency(node_ids: tuple[str, ...], arc_indices: Mapping[str, int],
               records: tuple[IntegerArc, ...],
               adjacency: Mapping[str, list[str]]) -> tuple[tuple[IntegerArc, ...], ...]:
    return tuple(tuple(records[arc_indices[a]] for a in adjacency.get(node, ())) for node in node_ids)


def prepare_integer_graph(nodes: Mapping[str, Node], segments: Mapping[str, Segment],
                          arcs: Mapping[str, Arc], outgoing: Mapping[str, list[str]],
                          incoming: Mapping[str, list[str]]) -> IntegerGraph:
    node_ids, segment_ids = tuple(nodes), tuple(segments)
    node_indices = {node: i for i, node in enumerate(node_ids)}
    segment_indices = {segment: i for i, segment in enumerate(segment_ids)}
    arc_indices = {arc: i for i, arc in enumerate(arcs)}
    records = tuple(_integer_arc(i, a, node_indices, segment_indices, segments[a['segment_id']])
                    for i, a in enumerate(arcs.values()))
    return IntegerGraph(node_ids, node_indices, segment_ids, segment_indices, arc_indices, tuple(arcs),
                        records, _adjacency(node_ids, arc_indices, records, outgoing),
                        _adjacency(node_ids, arc_indices, records, incoming))


def apply_integer_policy(base: IntegerGraph, outgoing: Mapping[str, list[str]],
                         incoming: Mapping[str, list[str]]) -> IntegerGraph:
    """Share topology and IDs when only policy eligibility changes."""
    return replace(base, outgoing=_adjacency(base.node_ids, base.arc_indices, base.arcs, outgoing),
                   incoming=_adjacency(base.node_ids, base.arc_indices, base.arcs, incoming))


def _extend_ids(count: int, extra: tuple[str, ...], indices: Mapping[str, int],
                additions: Mapping[str, object]) -> tuple[tuple[str, ...], Mapping[str, int]]:
    added = tuple(key for key in additions if key not in indices)
    if not added:
        return extra, indices
    return extra + added, ChainMap({key: count + i for i, key in enumerate(added)}, indices)


def integer_arc(graph: IntegerGraph, index: int) -> IntegerArc:
    overridden = graph.arc_overrides.get(index)
    if overridden is not None:
        return overridden
    return graph.arcs[index] if index < len(graph.arcs) else graph.extra_arcs[index - len(graph.arcs)]


def integer_node_id(graph: IntegerGraph, node: int) -> str:
    return graph.node_ids[node] if node < len(graph.node_ids) else graph.extra_node_ids[node - len(graph.node_ids)]


def integer_arc_id(graph: IntegerGraph, arc: int) -> str:
    return graph.arc_ids[arc] if arc < len(graph.arc_ids) else graph.extra_arc_ids[arc - len(graph.arc_ids)]


def integer_segment_id(graph: IntegerGraph, segment: int) -> str:
    if segment < len(graph.segment_ids):
        return graph.segment_ids[segment]
    return graph.extra_segment_ids[segment - len(graph.segment_ids)]


def _overlay_adjacency(graph: IntegerGraph, base: Mapping[int, tuple[IntegerArc, ...]],
                       updates: Mapping[str, list[str]], new_nodes: Mapping[str, Node]) -> dict[int, tuple[IntegerArc, ...]]:
    rows = dict(base)
    for node in new_nodes:
        rows.setdefault(graph.node_indices[node], ())
    for node, arc_ids in updates.items():
        rows[graph.node_indices[node]] = tuple(integer_arc(graph, graph.arc_indices[a]) for a in arc_ids)
    return rows


def extend_integer_graph(base: IntegerGraph | None, nodes: Mapping[str, Node],
                         segments: Mapping[str, Segment], arcs: Mapping[str, Arc],
                         outgoing: Mapping[str, list[str]], incoming: Mapping[str, list[str]], *,
                         segment_records: Mapping[str, Segment]) -> IntegerGraph:
    """Share prepared records and rows, replacing only overlay adjacency rows."""
    if base is None:
        raise ValueError('Graph has no prepared integer index')
    extra_nodes, node_indices = _extend_ids(len(base.node_ids) + len(base.extra_node_ids),
                                          base.extra_node_ids, base.node_indices, nodes)
    extra_segments, segment_indices = _extend_ids(len(base.segment_ids) + len(base.extra_segment_ids),
                                                base.extra_segment_ids, base.segment_indices, segments)
    records = list(base.extra_arcs)
    extra_arc_ids = list(base.extra_arc_ids)
    overrides = dict(base.arc_overrides)
    updates: dict[str, int] = {}
    for arc_id, arc in arcs.items():
        position = base.arc_indices.get(arc_id, len(base.arcs) + len(records))
        record = _integer_arc(position, arc, node_indices, segment_indices, segment_records[arc['segment_id']])
        if position == len(base.arcs) + len(records):
            records.append(record)
            extra_arc_ids.append(arc_id)
        else:
            overrides[position] = record
        updates[arc_id] = position
    arc_indices = ChainMap(updates, base.arc_indices)
    graph = replace(base, node_indices=node_indices, segment_indices=segment_indices,
                    arc_indices=arc_indices, extra_node_ids=extra_nodes, extra_segment_ids=extra_segments,
                    extra_arcs=tuple(records), extra_arc_ids=tuple(extra_arc_ids), arc_overrides=overrides)
    return replace(graph,
                   outgoing_overrides=_overlay_adjacency(graph, base.outgoing_overrides, outgoing, nodes),
                   incoming_overrides=_overlay_adjacency(graph, base.incoming_overrides, incoming, nodes))

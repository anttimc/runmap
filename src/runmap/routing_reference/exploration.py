"""Python exploration search and coalesced reference distance preparations."""

from collections import ChainMap
from concurrent.futures import Future
from dataclasses import replace
from time import monotonic

from runmap.paths.exploration import (
    DistanceData,
    ExplorationCache,
    Snap,
    VIRTUAL_START,
    legal_directions,
)
from runmap.routing.geometry import prepare_projected_geometry
from runmap.routing.graph import GraphData
from runmap.routing.integer_graph import extend_integer_graph
from runmap.routing_reference.search import indexed_shortest_distances


def split_start_graph(index: GraphData, snap: Snap) -> tuple[GraphData, str]:
    """Overlay temporary directed partial arcs; never modify prepared graph dictionaries."""
    segment = index.segments[snap.segment_id]
    a, b, length = segment["from"], segment["to"], segment["length_m"]
    # At an exact endpoint use the real node, preserving junction and access semantics.
    if snap.graph_offset_m <= 1e-8:
        return index, a
    if length - snap.graph_offset_m <= 1e-8:
        return index, b
    outgoing, incoming, arcs = {}, {}, {}
    for node in {a, b, VIRTUAL_START}:
        outgoing[node] = [arc_id for arc_id in index.outgoing.get(node, ()) if index.arcs[arc_id]["segment_id"] != snap.segment_id]
        incoming[node] = [arc_id for arc_id in index.incoming.get(node, ()) if index.arcs[arc_id]["segment_id"] != snap.segment_id]
    legal = legal_directions(index, snap.segment_id)
    for forward, first, second in ((True, a, VIRTUAL_START), (True, VIRTUAL_START, b),
                                   (False, b, VIRTUAL_START), (False, VIRTUAL_START, a)):
        if forward not in legal:
            continue
        left_part = a == first if forward else a == second
        part_length = snap.graph_offset_m if left_part else length - snap.graph_offset_m
        arc_id = f"{snap.segment_id}:split-{len(arcs)}:{'f' if forward else 'b'}"
        arcs[arc_id] = {"id": arc_id, "segment_id": snap.segment_id, "from": first, "to": second,
                        "length_m": part_length}
        outgoing[first].append(arc_id)
        incoming[second].append(arc_id)
    nodes = {VIRTUAL_START: {"id": VIRTUAL_START, "longitude": snap.longitude, "latitude": snap.latitude}}
    integer = extend_integer_graph(index.integer, nodes, {}, arcs, outgoing, incoming,
                                    segment_records=index.segments)
    return replace(index, nodes=ChainMap(nodes, index.nodes), arcs=ChainMap(arcs, index.arcs),
                   outgoing=ChainMap(outgoing, index.outgoing), incoming=ChainMap(incoming, index.incoming),
                   projected=prepare_projected_geometry(nodes, {}, arcs.values(), integer, index.projected),
                   integer=integer), VIRTUAL_START


def distance_data(index: GraphData, snap: Snap, maximum_m: float, deadline: float | None = None) -> DistanceData:
    graph, start = split_start_graph(index, snap)
    assert graph.integer is not None
    return DistanceData(snap, maximum_m,
                        indexed_shortest_distances(graph, graph.integer.node_indices[start], maximum_m, deadline=deadline))


def cached_distances(cache: ExplorationCache, dataset_id: str, index: GraphData, snap: Snap,
                     maximum_m: float, deadline: float | None = None) -> tuple[DistanceData, bool]:
    key = (dataset_id, index.profile["id"], snap.segment_id, snap.graph_offset_m)
    pending_key = (*key, maximum_m)
    with cache.lock:
        existing = cache.entries.get(key)
        if existing and existing.maximum_m >= maximum_m:
            cache.entries.move_to_end(key)
            return existing, True
        future = cache.pending.get(pending_key)
        owner = future is None
        if future is None:
            future = Future()
            cache.pending[pending_key] = future
    if not owner:
        timeout = max(0, deadline - monotonic()) if deadline is not None else None
        return future.result(timeout=timeout), True
    try:
        result = distance_data(index, snap, maximum_m, deadline)
        with cache.lock:
            existing = cache.entries.get(key)
            if existing is None or existing.maximum_m < maximum_m:
                cache.entries[key] = result
            cache.entries.move_to_end(key)
            while len(cache.entries) > cache.capacity:
                cache.entries.popitem(last=False)
            future.set_result(result)
        return result, False
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with cache.lock:
            cache.pending.pop(pending_key, None)

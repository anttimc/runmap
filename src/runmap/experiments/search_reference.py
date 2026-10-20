"""String-indexed A* retained as the baseline for integer-index benchmarks."""

from collections.abc import Callable, Mapping, Set
import heapq
import math
from time import monotonic

from runmap.routing.search import CostModel, GraphData, PathResult, _arc_cost, _remaining_cost
from runmap.routing.types import Arc


def _path_arc_ids(index: GraphData, predecessors: Mapping[str, str], start: str,
                   destination: str) -> tuple[str, ...]:
    arcs: list[str] = []
    node = destination
    while node != start:
        arc = predecessors[node]
        arcs.append(arc)
        node = index.arcs[arc]['from']
    return tuple(reversed(arcs))


def shortest_path_string(
    index: GraphData, start: str, destination: str, model: CostModel | None = None,
    overlap_segments: Set[str] = frozenset(), overlap_factor: float = 1.0,
    blocked_segments: Set[str] = frozenset(), *,
    heuristic: Callable[[str, str], float] | None = None,
    arc_cost: Callable[[Arc], float] | None = None, deadline: float | None = None,
) -> PathResult | None:
    """Keep the pre-integer search behavior available outside production dispatch."""
    if start not in index.nodes or destination not in index.nodes:
        raise ValueError('Route endpoint is not in the graph')
    if overlap_factor < 1:
        raise ValueError('Overlap factor must be at least one')
    if start in index.blocked_nodes or destination in index.blocked_nodes:
        return None
    model = model or CostModel()
    distances = {start: 0.0}
    predecessors: dict[str, str] = {}
    if _remaining_cost(heuristic, destination, destination) != 0:
        raise ValueError('Heuristic must be zero at the destination')
    queue = [(_remaining_cost(heuristic, start, destination), 0.0, start)]
    expanded = 0
    while queue:
        if deadline is not None and monotonic() >= deadline:
            raise TimeoutError('Route search exceeded its deadline')
        _, distance, node = heapq.heappop(queue)
        if distance != distances.get(node):
            continue
        if node == destination:
            break
        expanded += 1
        for arc_id in index.outgoing.get(node, ()):
            arc = index.arcs[arc_id]
            if arc['segment_id'] in blocked_segments:
                continue
            cost = arc_cost(arc) if arc_cost is not None else _arc_cost(
                index, arc, model, overlap_segments, overlap_factor)
            if not math.isfinite(cost) or cost < 0:
                raise ValueError('Arc cost must be finite and non-negative')
            candidate = distance + cost
            if candidate < distances.get(arc['to'], math.inf):
                distances[arc['to']] = candidate
                predecessors[arc['to']] = arc_id
                priority = candidate + _remaining_cost(heuristic, arc['to'], destination)
                heapq.heappush(queue, (priority, candidate, arc['to']))
    if destination not in distances:
        return None
    return PathResult(_path_arc_ids(index, predecessors, start, destination), distances[destination], expanded)

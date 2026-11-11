"""Reference A* over arrival movements, independent of the native label storage."""

from collections.abc import Callable
import heapq
import math

from runmap.routing.deadlines import check_deadline
from runmap.routing.graph import GraphData, arc_usable
from runmap.routing.integer_graph import IntegerArc, integer_arc
from runmap.routing_reference.search import PathResult


def signal_shortest_path(graph: GraphData, start: str, destination: str, *,
                         arc_cost: Callable[[IntegerArc], float],
                         transition_cost: Callable[[IntegerArc | None, IntegerArc], float],
                         heuristic: Callable[[int, int], float], initial_arc: str | None = None,
                         visit_node: Callable[[int], None] | None = None,
                         deadline: float | None = None) -> PathResult | None:
    index = graph.integer
    if index is None:
        raise ValueError("Signal search requires an integer graph")
    if start in graph.blocked_nodes or destination in graph.blocked_nodes:
        return None
    initial = (start, initial_arc)
    best: dict[tuple[str, str | None], float] = {initial: 0.0}
    predecessors: dict[tuple[str, str | None], tuple[str, str | None]] = {}
    queue: list[tuple[float, float, int, tuple[str, str | None]]] = [(0.0, 0.0, 0, initial)]
    serial, expanded = 0, 0
    while queue:
        check_deadline(deadline)
        _, distance, _, state = heapq.heappop(queue)
        if distance != best[state]:
            continue
        node, prior = state
        if visit_node is not None:
            visit_node(index.node_indices[node])
        if node == destination:
            path: list[str] = []
            while state != initial:
                assert state[1] is not None
                path.append(state[1])
                state = predecessors[state]
            return PathResult(tuple(reversed(path)), distance, expanded)
        expanded += 1
        previous = integer_arc(index, index.arc_indices[prior]) if prior is not None else None
        for key in graph.outgoing.get(node, ()):
            record = graph.arcs[key]
            if not arc_usable(graph, record):
                continue
            arc = integer_arc(index, index.arc_indices[key])
            if (state != initial and previous is not None and previous.segment == arc.segment
                    and previous.reverse != arc.reverse):
                continue
            cost = arc_cost(arc) + transition_cost(previous, arc)
            if not math.isfinite(cost) or cost < arc.length_m:
                raise ValueError("Signal cost must be finite and at least physical length")
            candidate = distance + cost
            following = record["to"], key
            if candidate < best.get(following, math.inf):
                best[following] = candidate
                predecessors[following] = state
                serial += 1
                heapq.heappush(queue, (candidate + heuristic(arc.target, index.node_indices[destination]),
                                      candidate, serial, following))
    return None

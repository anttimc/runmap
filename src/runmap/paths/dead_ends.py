"""Classify building-access branches for optional display hiding."""

from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from runmap.routing.profiles import exploration_profile
from runmap.routing.graph import GraphData, apply_profile
from runmap.routing.types import Coordinate


CLASSIFIER_VERSION = "building-access-v1"
ACCESS_POLICY = "public:all:stairs-0"
ENTRANCE_LIMIT_M = 150.0


@dataclass(frozen=True)
class Classification:
    reason: str
    branch_id: str
    attachment_node: str
    branch_length_m: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class PeeledEdge:
    segment_id: str
    child: str
    parent: str


def _peel(index: GraphData) -> tuple[list[PeeledEdge], set[str]]:
    legal = {segment for segment, directions in index.direction_cache.items() if directions}
    incident: dict[str, set[str]] = defaultdict(set)
    degree: dict[str, int] = defaultdict(int)
    for segment_id in legal:
        segment = index.segments[segment_id]
        for node in (segment["from"], segment["to"]):
            incident[node].add(segment_id)
            degree[node] += 1  # A self-loop contributes twice.
    queue = deque(node for node, count in degree.items() if count == 1)
    peeled = []
    while queue:
        child = queue.popleft()
        if degree[child] != 1:
            continue
        segment_id = next(iter(incident[child]))
        segment = index.segments[segment_id]
        parent = segment["to"] if segment["from"] == child else segment["from"]
        peeled.append(PeeledEdge(segment_id, child, parent))
        for node in (child, parent):
            incident[node].remove(segment_id)
            degree[node] -= 1
            if degree[node] == 1:
                queue.append(node)
    return peeled, {node for node, count in degree.items() if count > 0}


def _attached_edges(peeled: list[PeeledEdge], core: set[str]) -> dict[str, str]:
    roots: dict[str, str] = {}
    for edge in reversed(peeled):
        if edge.parent in core:
            roots[edge.child] = edge.segment_id
        elif edge.parent in roots:
            roots[edge.child] = roots[edge.parent]
    return roots


def _candidate_reasons(
    index: GraphData, peeled: list[PeeledEdge], entrance_limit_m: float,
) -> dict[str, str]:
    children: dict[str, list[str]] = defaultdict(list)
    lengths: dict[str, float] = {}
    entrances: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    chain_prefix = _entrance_chain_prefix(index, peeled)
    for edge in peeled:
        segment = index.segments[edge.segment_id]
        tags = {key: value.strip().lower() for key, value in segment["tags"].items()}
        descendants = children[edge.child]
        length = segment["length_m"] + sum(lengths[item] for item in descendants)
        active_entrance = str(index.nodes[edge.child].get("entrance", "")).strip().lower()
        entrance = (all(entrances[item] for item in descendants) if descendants else
                    active_entrance not in {"", "no", "exit", "false", "0", "disused", "abandoned"})
        entrance &= tags.get("highway") in {"footway", "service", "steps"}
        entrances[edge.segment_id] = entrance
        lengths[edge.segment_id] = length
        all_children = all(item in reasons for item in descendants)
        if all_children and tags.get("highway") == "service" and tags.get("service") == "driveway":
            reasons[edge.segment_id] = "building_access_driveway"
        elif all_children and entrance and length + chain_prefix[edge.segment_id] <= entrance_limit_m:
            reasons[edge.segment_id] = "building_access_entrance"
        children[edge.parent].append(edge.segment_id)
    return reasons


def _entrance_chain_prefix(index: GraphData, peeled: list[PeeledEdge]) -> dict[str, float]:
    """Apply the length limit to a whole terminal chain, not just its last segment."""
    children: dict[str, int] = defaultdict(int)
    parents = {edge.child: edge.segment_id for edge in peeled}
    for edge in peeled:
        children[edge.parent] += 1
    prefix: dict[str, float] = {}
    for edge in reversed(peeled):
        parent_id = parents.get(edge.parent)
        parent = index.segments[parent_id] if parent_id else None
        compatible = parent is not None and parent["tags"].get("highway", "").strip().lower() in {
            "footway", "service", "steps"}
        prefix[edge.segment_id] = (
            prefix[parent_id] + parent["length_m"]
            if parent_id and parent and compatible and children[edge.parent] == 1 else 0.0)
    return prefix


def classify_building_access(
    routing: GraphData,
    near_boundary: Callable[[Sequence[Coordinate]], bool],
    reliable_coverage: bool,
    entrance_limit_m: float = ENTRANCE_LIMIT_M,
) -> dict[str, Classification]:
    """Keep ambiguous components and boundary trees; classify independent terminal twigs."""
    if not reliable_coverage:
        return {}
    index = apply_profile(routing, exploration_profile(avoid_stairs=False))
    peeled, core = _peel(index)
    roots = _attached_edges(peeled, core)
    protected = {roots[edge.child] for edge in peeled if edge.child in roots
                 and near_boundary(index.segments[edge.segment_id]["geometry"])}
    reasons = _candidate_reasons(index, peeled, entrance_limit_m)
    parent_edges = {edge.child: edge.segment_id for edge in peeled}
    groups: dict[str, str] = {}
    attachments: dict[str, str] = {}
    lengths: dict[str, float] = defaultdict(float)
    for edge in reversed(peeled):
        if edge.child not in roots or roots[edge.child] in protected or edge.segment_id not in reasons:
            continue
        group = groups.get(parent_edges.get(edge.parent, ""), edge.segment_id)
        groups[edge.segment_id] = group
        attachments.setdefault(group, edge.parent)
        lengths[group] += index.segments[edge.segment_id]["length_m"]
    return {segment_id: Classification(
        reasons[segment_id], group, attachments[group], lengths[group],
        ("terminal_public_pedestrian_tree", "service=driveway" if reasons[segment_id].endswith("driveway")
         else f"active_terminal_entrance;chain_limit_m={entrance_limit_m:g}"),
    ) for segment_id, group in groups.items()}

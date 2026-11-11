"""Compile full-dataset OSM crossing evidence into canonical gates and movements."""

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from runmap.routing.signals import SignalGate, SignalTurn
from runmap.routing.types import Tags


@dataclass(frozen=True)
class SignalSegment:
    number: int
    source: int
    target: int
    length_m: float
    way: str
    tags: Tags


@dataclass(frozen=True)
class PreparedSignalStage:
    identifier: str
    gates: tuple[SignalGate, ...]
    turns: tuple[SignalTurn, ...]
    evidence: str


def signal_evidence(tags: Mapping[str, str]) -> bool:
    return ("traffic_signals" in tags.get("crossing", "").split(";")
            or tags.get("crossing:signals") == "yes")


def _crossing(tags: Mapping[str, str]) -> bool:
    return (tags.get("footway") == "crossing" or tags.get("cycleway") == "crossing"
            or signal_evidence(tags))


def _uncertain(tags: Mapping[str, str]) -> bool:
    return (bool(tags.get("railway")) or tags.get("crossing:island") == "yes"
            or (signal_evidence(tags) and tags.get("crossing:signals") == "no"))


def _gate(segments: Sequence[SignalSegment], nodes: Mapping[int, Tags],
          incident: Mapping[int, Sequence[SignalSegment]], unresolved: bool) -> SignalGate:
    """Place one interior boundary on a connected crossing chain."""
    ordered = sorted(segments, key=lambda s: s.number)
    segment = ordered[len(ordered) // 2]
    directions = {nodes.get(n, {}).get("traffic_signals:direction")
                  for s in segments for n in (s.source, s.target)} - {None}
    ambiguous_direction = any(nodes.get(n, {}).get("traffic_signals:direction")
                              and len({s.way for s in incident[n]}) != 1
                              for s in segments for n in (s.source, s.target))
    mask = 1 if directions == {"forward"} else 2 if directions == {"backward"} else 3
    if ambiguous_direction:
        mask = 3
    return SignalGate(segment.number, segment.length_m / 2,
                      unresolved or ambiguous_direction or bool(directions - {"forward", "backward", "both"}), mask)


def _components(members: Sequence[SignalSegment], incident: Mapping[int, Sequence[SignalSegment]]
                ) -> Iterator[list[SignalSegment]]:
    remaining = {s.number: s for s in members}
    while remaining:
        first = remaining.pop(min(remaining))
        component = [first]
        frontier = [first.source, first.target]
        while frontier:
            node = frontier.pop()
            for other in incident[node]:
                if other.number in remaining:
                    component.append(remaining.pop(other.number))
                    frontier.extend((other.source, other.target))
        yield component


def _way_stages(crossing: Mapping[str, Sequence[SignalSegment]], nodes: Mapping[int, Tags],
                incident: Mapping[int, Sequence[SignalSegment]]
                ) -> tuple[list[PreparedSignalStage], set[int]]:
    stages: list[PreparedSignalStage] = []
    covered: set[int] = set()
    for way, members in sorted(crossing.items()):
        for component in _components(members, incident):
            vertices = {n for s in component for n in (s.source, s.target)}
            evidence = [s.tags for s in component] + [nodes.get(n, {}) for n in vertices]
            if not any(signal_evidence(t) or t.get("highway") == "traffic_signals" for t in evidence):
                continue
            degree = {n: sum(n in (s.source, s.target) for s in component) for n in vertices}
            unresolved = any(_uncertain(t) for t in evidence) or any(d > 2 for d in degree.values())
            stages.append(PreparedSignalStage(f"way:{way}:{min(s.number for s in component)}",
                (_gate(component, nodes, incident, unresolved),), (), "crossing-way"))
            covered.update(vertices)
    return stages, covered


def _paired_stages(segments: Sequence[SignalSegment], nodes: Mapping[int, Tags],
                   incident: Mapping[int, Sequence[SignalSegment]], covered: set[int]
                   ) -> tuple[list[PreparedSignalStage], set[int]]:
    stages: list[PreparedSignalStage] = []
    paired: set[int] = set()
    for segment in segments:
        a, b = nodes.get(segment.source, {}), nodes.get(segment.target, {})
        if (segment.source not in covered and segment.target not in covered
                and segment.source not in paired and segment.target not in paired
                and signal_evidence(a) and signal_evidence(b)
                and len(incident[segment.source]) == len(incident[segment.target]) == 2):
            stages.append(PreparedSignalStage(f"pair:{segment.number}",
                (_gate((segment,), nodes, incident, _uncertain(a) or _uncertain(b)),), (),
                "adjacent-signal-nodes"))
            paired.update((segment.source, segment.target))
    return stages, paired


def _node_stage(node: int, tags: Tags, links: Sequence[SignalSegment], *, covered: bool = False
                ) -> PreparedSignalStage | None:
    explicit = signal_evidence(tags)
    generic = tags.get("highway") == "traffic_signals"
    if not explicit and not generic:
        return None
    pedestrian = [s for s in links if s.tags.get("highway") in
                  {"footway", "cycleway", "path", "pedestrian", "steps"}]
    candidates = pedestrian if len(pedestrian) >= 2 else links
    # Generic vehicle lights alone do not establish pedestrian movements.
    if generic and not explicit and len(pedestrian) < 2:
        return None
    direction = tags.get("traffic_signals:direction", "both")
    primary = {s.number for s in candidates}
    pedestrian_ids = {s.number for s in pedestrian}
    turns = tuple(SignalTurn(node, a.number, b.number,
                  _uncertain(tags) or not explicit or len(candidates) != 2
                  or a.number not in primary or b.number not in primary
                  or (direction != "both" and a.way != b.way)
                  or direction not in {"forward", "backward", "both"})
                  for a in (links if explicit else candidates) for b in (links if explicit else candidates)
                  if a.number != b.number
                  and (not covered or (a.number in pedestrian_ids) != (b.number in pedestrian_ids))
                  and (direction not in {"forward", "backward"} or a.way != b.way
                       or (direction == "forward" and a.target == b.source == node)
                       or (direction == "backward" and a.source == b.target == node)))
    return PreparedSignalStage(f"node:{node}", (), turns, "crossing-node") if turns else None


def prepare_signal_stages(segments: Sequence[SignalSegment], nodes: Mapping[int, Tags]
                          ) -> tuple[PreparedSignalStage, ...]:
    incident: dict[int, list[SignalSegment]] = defaultdict(list)
    crossing: dict[str, list[SignalSegment]] = defaultdict(list)
    for segment in segments:
        incident[segment.source].append(segment)
        incident[segment.target].append(segment)
        if _crossing(segment.tags):
            crossing[segment.way].append(segment)
    stages, covered = _way_stages(crossing, nodes, incident)
    pairs, paired = _paired_stages(segments, nodes, incident, covered)
    stages.extend(pairs)
    for node, tags in sorted(nodes.items()):
        if node not in paired:
            stage = _node_stage(node, tags, incident.get(node, ()), covered=node in covered)
            if stage is not None:
                stages.append(stage)
    return tuple(stages)

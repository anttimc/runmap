"""Directed signal charging boundaries shared by preparation and reference routing."""

from collections.abc import Mapping
from dataclasses import dataclass
import math


SIGNAL_MODEL = "directed-signal-passages-v2"


@dataclass(frozen=True)
class SignalGate:
    segment: int
    offset_m: float
    unresolved: bool = False
    directions: int = 3


@dataclass(frozen=True)
class SignalTurn:
    node: int
    incoming: int
    outgoing: int
    unresolved: bool = False


@dataclass(frozen=True)
class SignalData:
    gates: Mapping[int, tuple[SignalGate, ...]]
    turns: Mapping[tuple[int, int, int], SignalTurn]


@dataclass(frozen=True)
class SignalTraversal:
    segment: int
    start_m: float
    end_m: float
    source: int


def signal_counts(data: SignalData, previous: SignalTraversal | None,
                  current: SignalTraversal) -> tuple[int, int]:
    """Charge upon passing a boundary; stopping or reversing on it is free."""
    if not all(math.isfinite(v) and v >= 0 for v in (current.start_m, current.end_m)):
        raise ValueError("Invalid signal traversal chainage")
    resolved, unresolved = 0, 0
    if current.start_m == current.end_m:
        return resolved, unresolved
    forward = current.end_m > current.start_m
    for gate in data.gates.get(current.segment, ()):
        if not gate.directions & (1 if forward else 2):
            continue
        crossed = min(current.start_m, current.end_m) < gate.offset_m < max(current.start_m, current.end_m)
        continued = (current.start_m == gate.offset_m and previous is not None
                     and previous.segment == current.segment and previous.end_m == gate.offset_m
                     and (previous.end_m > previous.start_m) == forward)
        if crossed or continued:
            unresolved += int(gate.unresolved)
            resolved += int(not gate.unresolved)
    if previous is not None and previous.segment != current.segment:
        turn = data.turns.get((current.source, previous.segment, current.segment))
        if turn is not None:
            unresolved += int(turn.unresolved)
            resolved += int(not turn.unresolved)
    return resolved, unresolved

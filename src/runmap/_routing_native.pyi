"""Typed signatures for scalar integration and reusable native chunks."""

from collections.abc import Sequence
from runmap.paths.environment_join import EnvironmentInterval


def environment_unknown_lengths(
    values: Sequence[tuple[int, Sequence[EnvironmentInterval]]],
    lengths: Sequence[tuple[int, float]],
    spans: Sequence[tuple[int, float, float]], *, timeout_seconds: float | None = None,
) -> dict[str, bytes]: ...


class NumericChunk:
    def __init__(self, payload: bytes, *, timeout_seconds: float | None = None) -> None: ...
    @property
    def resident_bytes(self) -> int: ...
    @property
    def payload_bytes(self) -> int: ...


def integrate_fields(
    points: Sequence[tuple[float, float]],
    home: tuple[float, float],
    destination: tuple[float, float],
    radius: float,
    transition_width: float,
    spacing_m: float = 10.0,
    timeout_seconds: float | None = None,
) -> tuple[float, float, float]: ...


class RoutingInputChunk:
    def __init__(self, payload: bytes, *, timeout_seconds: float | None = None) -> None: ...
    @property
    def resident_bytes(self) -> int: ...


class NumericGraph:
    def __init__(
        self, payloads: Sequence[bytes], *, bounds: tuple[float, float, float, float] | None = None,
        distance_scale: float = 0.0, timeout_seconds: float | None = None,
    ) -> None: ...
    @staticmethod
    def from_chunks(
        chunks: Sequence[NumericChunk], *, bounds: tuple[float, float, float, float] | None = None,
        distance_scale: float = 0.0, timeout_seconds: float | None = None,
    ) -> NumericGraph: ...
    @property
    def input_bytes(self) -> int: ...
    @property
    def chunk_bytes(self) -> int: ...
    @property
    def index_bytes(self) -> int: ...
    @property
    def node_count(self) -> int: ...
    @property
    def segment_count(self) -> int: ...
    @property
    def has_frontier(self) -> bool: ...
    @property
    def environment_prepared(self) -> bool: ...
    @property
    def signals_prepared(self) -> bool: ...
    def set_input_chunks(
        self, chunks: Sequence[RoutingInputChunk], *, timeout_seconds: float | None = None,
    ) -> None: ...
    def selected_segment_ids(self) -> bytes: ...
    def set_environment_intervals(self, values: Sequence[tuple[int, Sequence[EnvironmentInterval]]]) -> None: ...
    def environment_fields(self) -> list[str]: ...
    def set_signals(
        self, gates: Sequence[tuple[int, float, bool, int]], turns: Sequence[tuple[int, int, int, bool]],
    ) -> None: ...
    def distances(
        self, start_node: int | None = None, *, start_segment: tuple[int, float, float | None] | None = None,
        profile: int = 0, maximum: float = ..., reverse: bool = False, timeout_seconds: float | None = None,
    ) -> tuple[bytes, bytes, bytes, int, int]: ...
    def shortest_path(
        self, start_node: int | None = None, destination_node: int | None = None, *,
        start_segment: tuple[int, float, float | None] | None = None,
        destination_segment: tuple[int, float, float | None] | None = None,
        start_point: tuple[float, float] | None = None, destination_point: tuple[float, float] | None = None,
        profile: int = 0, hill_weight: float = 0.0, environment_weights: dict[str, float] | None = None,
        shape: tuple[tuple[float, float], tuple[float, float], float, float, float, float, float, float, bool] | None = None,
        used_segments: Sequence[int] = (), used_spans: Sequence[tuple[int, float, float]] = (),
        hill_intervals: Sequence[tuple[int, bytes]] = (), signal_weight: float = 0.0,
        signal_arrival: tuple[int, float, float] | None = None, timeout_seconds: float | None = None,
    ) -> tuple[float | None, int, bytes, bytes, bytes, bytes, bytes, bytes, dict[str, tuple[bytes, bytes]], bytes, int, int]: ...

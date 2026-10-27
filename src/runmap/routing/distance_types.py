"""Immutable reference distance buffers retained by exploration snapshots."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeDistances:
    """Immutable float64 node values and int64 discovery order, bound to shared IDs."""

    node_ids: tuple[str, ...]
    extra_node_ids: tuple[str, ...]
    values: memoryview
    reached: memoryview

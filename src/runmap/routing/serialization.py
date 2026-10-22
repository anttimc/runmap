"""Materialize prepared array views at published JSON boundaries."""

from runmap.routing.arc_storage import ArcRecords
from runmap.routing.coordinates import PackedLine
from runmap.routing.node_storage import NodeRecords
from runmap.routing.segment_storage import SegmentRecords
from runmap.routing.types import Arc, Coordinate, Node, Segment


def prepared_json(value: object) -> list[Coordinate] | list[Arc] | list[Node] | list[Segment]:
    if isinstance(value, (PackedLine, ArcRecords, NodeRecords, SegmentRecords)):
        return list(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

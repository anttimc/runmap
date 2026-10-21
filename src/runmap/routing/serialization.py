"""Materialize prepared array views at published JSON boundaries."""

from runmap.routing.arc_storage import ArcRecords
from runmap.routing.coordinates import PackedLine
from runmap.routing.types import Arc, Coordinate


def prepared_json(value: object) -> list[Coordinate] | list[Arc]:
    if isinstance(value, (PackedLine, ArcRecords)):
        return list(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

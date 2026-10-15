"""Explicit request and leg data for shape-cost evaluation."""

from dataclasses import dataclass, field

from runmap.routing.fields import Point
from runmap.routing.search import GraphData


@dataclass(frozen=True)
class ShapeStrengths:
    centre: float
    side: float
    reuse: float


@dataclass(frozen=True)
class ShapeFeatures:
    radial_integral_m: float
    right_integral_m: float
    projected_length_m: float


@dataclass(frozen=True)
class RequestCostData:
    graph: GraphData
    home: Point
    turnaround: Point
    radius_m: float
    transition_width_m: float
    spacing_m: float
    strengths: ShapeStrengths
    distance_only: bool


@dataclass
class RequestCostCache:
    shape_fields: dict[str, ShapeFeatures] = field(default_factory=dict)


@dataclass(frozen=True)
class LegCostContext:
    penalize_right: bool
    used_segments: frozenset[str] = frozenset()

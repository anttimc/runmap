"""Reference planner overlays, feature caches, and leg-local cost context."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from runmap.routing.cost_types import (
    PreferenceSettings,
    PreferenceWeightTables,
    PreferenceWeights,
    ResolvedPreferences,
    ShapeStrengths,
)
from runmap.routing.graph import GraphData
from runmap.paths.environment_join import EnvironmentInterval
from runmap.routing.environment_costs import EnvironmentCost, EnvironmentWeights
from runmap.routing.hill_types import HillData, HillFeatures
from runmap.routing.integer_graph import IntegerGraph
from runmap.routing.types import Point


@dataclass(frozen=True)
class PhysicalPart:
    segment: int
    start_m: float
    end_m: float


@dataclass(frozen=True, slots=True)
class IndexedTraversal:
    segment: int
    graph_start_m: float
    graph_end_m: float
    reverse: bool


@dataclass(frozen=True)
class ShapeFeatures:
    radial_integral_m: float
    right_integral_m: float
    projected_length_m: float


@dataclass(frozen=True)
class RequestCostData:
    graph: GraphData
    index: IntegerGraph
    home: Point
    turnaround: Point
    radius_m: float
    transition_width_m: float
    spacing_m: float
    strengths: ShapeStrengths
    shape_disabled: bool
    parts: Mapping[int, PhysicalPart] = field(default_factory=dict)
    preferences: ResolvedPreferences = ResolvedPreferences(
        PreferenceSettings(), PreferenceWeights(), PreferenceWeightTables())
    hills: HillData | None = None
    environment: Mapping[int, tuple[EnvironmentInterval, ...]] | None = None
    environment_weights: EnvironmentWeights = EnvironmentWeights()


@dataclass
class RequestCostCache:
    shape_fields: dict[int, ShapeFeatures] = field(default_factory=dict)
    hill_features: dict[int, HillFeatures] = field(default_factory=dict)
    environment_costs: dict[int, EnvironmentCost] = field(default_factory=dict)


@dataclass(frozen=True)
class LegCostContext:
    penalize_right: bool
    used_segments: frozenset[int] = frozenset()

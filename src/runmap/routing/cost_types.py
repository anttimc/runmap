"""Explicit request and leg data for shape-cost evaluation."""

from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Literal

from runmap.routing.fields import Point
from runmap.routing.search import GraphData
from runmap.routing.hill_types import HillData, HillFeatures


PreferenceLevel = Literal["off", "low", "high"]


@dataclass(frozen=True)
class PhysicalPart:
    segment_id: str
    start_m: float
    end_m: float


@dataclass(frozen=True)
class PreferenceSettings:
    hills: PreferenceLevel = "off"
    signals: PreferenceLevel = "off"
    busy_roads: PreferenceLevel = "off"
    greenery: PreferenceLevel = "off"


@dataclass(frozen=True)
class PreferenceWeights:
    hills: float = 0.0
    signals: float = 0.0
    busy_roads: float = 0.0
    greenery: float = 0.0


@dataclass(frozen=True)
class PreferenceWeightTable:
    version: str
    off: float = 0.0
    low: float | None = None
    high: float | None = None


@dataclass(frozen=True)
class PreferenceWeightTables:
    hills: PreferenceWeightTable = PreferenceWeightTable("minetti-excess-equivalent-metres-v4", low=1.0, high=6.0)
    signals: PreferenceWeightTable = PreferenceWeightTable("signals-off-v1")
    busy_roads: PreferenceWeightTable = PreferenceWeightTable("busy-roads-off-v1")
    greenery: PreferenceWeightTable = PreferenceWeightTable("greenery-off-v1")


@dataclass(frozen=True)
class PreferenceCapability:
    supported_levels: tuple[PreferenceLevel, ...]
    feature_version: str | None
    coverage_fraction: float | None
    unavailable_reason: str | None


@dataclass(frozen=True)
class PreferenceCapabilities:
    hills: PreferenceCapability
    signals: PreferenceCapability
    busy_roads: PreferenceCapability
    greenery: PreferenceCapability


@dataclass(frozen=True)
class ResolvedPreferences:
    settings: PreferenceSettings
    weights: PreferenceWeights
    weight_tables: PreferenceWeightTables


@dataclass(frozen=True)
class TraversalRef:
    segment_id: str
    graph_start_m: float
    graph_end_m: float
    reverse: bool


@dataclass(frozen=True)
class PreferenceCosts:
    hills: float = 0.0
    signals: float = 0.0
    busy_roads: float = 0.0
    non_green: float = 0.0


@dataclass(frozen=True)
class PreferenceObservations:
    """Null observations distinguish unavailable features from measured zero."""

    estimated_ascent_m: float | None = None
    estimated_descent_m: float | None = None
    terrain_valid_fraction: float | None = None
    terrain_trusted_fraction: float | None = None
    terrain_fallback_distance_m: float | None = None
    signal_passages: int | None = None
    unresolved_signal_passages: int | None = None
    busy_road_exposure_m: float | None = None
    busy_road_coverage_fraction: float | None = None
    green_context_distance_m: float | None = None
    green_context_coverage_fraction: float | None = None


@dataclass(frozen=True)
class CostBreakdown:
    distance_m: float = 0.0
    centre: float = 0.0
    side: float = 0.0
    reuse: float = 0.0
    hills: float = 0.0
    signals: float = 0.0
    busy_roads: float = 0.0
    non_green: float = 0.0
    total: float = 0.0


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
    shape_disabled: bool
    parts: Mapping[str, PhysicalPart] = field(default_factory=dict)
    preferences: ResolvedPreferences = ResolvedPreferences(
        PreferenceSettings(), PreferenceWeights(), PreferenceWeightTables())
    hills: HillData | None = None


@dataclass
class RequestCostCache:
    shape_fields: dict[str, ShapeFeatures] = field(default_factory=dict)
    hill_features: dict[str, HillFeatures] = field(default_factory=dict)


@dataclass(frozen=True)
class LegCostContext:
    penalize_right: bool
    used_segments: frozenset[str] = frozenset()

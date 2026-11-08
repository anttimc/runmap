"""Preferences, capabilities, cost breakdowns, and observations shared by routing backends."""

from dataclasses import dataclass
from typing import Literal


PreferenceLevel = Literal["off", "low", "high"]


@dataclass(frozen=True)
class PreferenceSettings:
    hills: PreferenceLevel = "off"
    signals: PreferenceLevel = "off"
    busy_roads: PreferenceLevel = "off"
    greenery: PreferenceLevel = "off"
    environment_greenery: PreferenceLevel = "off"
    environment_water: PreferenceLevel = "off"
    environment_traffic: PreferenceLevel = "off"
    environment_wetland: PreferenceLevel = "off"


@dataclass(frozen=True)
class PreferenceWeights:
    hills: float = 0.0
    signals: float = 0.0
    busy_roads: float = 0.0
    greenery: float = 0.0
    environment_greenery: float = 0.0
    environment_water: float = 0.0
    environment_traffic: float = 0.0
    environment_wetland: float = 0.0


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
    environment_greenery: PreferenceWeightTable = PreferenceWeightTable("environment-levels-v1", low=0.25, high=1.0)
    environment_water: PreferenceWeightTable = PreferenceWeightTable("environment-levels-v1", low=0.25, high=1.0)
    environment_traffic: PreferenceWeightTable = PreferenceWeightTable("environment-traffic-levels-v2", low=0.25, high=4.0)
    environment_wetland: PreferenceWeightTable = PreferenceWeightTable("environment-levels-v1", low=0.25, high=1.0)


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
    environment_greenery: PreferenceCapability = PreferenceCapability(("off",), None, None, "Prepared environment data is unavailable.")
    environment_water: PreferenceCapability = PreferenceCapability(("off",), None, None, "Prepared environment data is unavailable.")
    environment_traffic: PreferenceCapability = PreferenceCapability(("off",), None, None, "Prepared environment data is unavailable.")
    environment_wetland: PreferenceCapability = PreferenceCapability(("off",), None, None, "Prepared environment data is unavailable.")


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
    environment_greenery: float = 0.0
    environment_water: float = 0.0
    environment_traffic: float = 0.0
    environment_wetland: float = 0.0


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
    environment_unknown_greenery_m: float | None = None
    environment_unknown_water_m: float | None = None
    environment_unknown_traffic_m: float | None = None
    environment_unknown_wetland_m: float | None = None


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
    environment_greenery: float = 0.0
    environment_water: float = 0.0
    environment_traffic: float = 0.0
    environment_wetland: float = 0.0


@dataclass(frozen=True)
class ShapeStrengths:
    centre: float
    side: float
    reuse: float

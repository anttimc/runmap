"""Validate preference capabilities and resolve independently versioned weights."""

import math

from runmap.routing.cost_types import (
    PreferenceCapabilities, PreferenceCapability, PreferenceLevel, PreferenceSettings,
    PreferenceWeights, PreferenceWeightTable, PreferenceWeightTables, ResolvedPreferences,
)
from runmap.routing.hill_types import HillData


COST_MODEL_VERSION = "distance-preferences-v1"
COST_UNITS = "equivalent_metres"


def preference_capabilities(hills: HillData | None = None) -> PreferenceCapabilities:
    """Advertise only implemented algorithms with verified feature support."""
    return PreferenceCapabilities(
        (PreferenceCapability(("off", "low", "high"), hills.model.version, hills.valid_fraction, None)
         if hills is not None else PreferenceCapability(("off",), None, None, "Prepared hill measurements are unavailable.")),
        PreferenceCapability(("off",), None, None, "Crossing passage attribution is not prepared."),
        PreferenceCapability(("off",), None, None, "Buffered road context is not prepared."),
        PreferenceCapability(("off",), None, None, "Buffered green-area context is not prepared."),
    )


def validate_weight_table(table: PreferenceWeightTable) -> None:
    if not table.version:
        raise ValueError("Preference weight table needs a version")
    if table.off != 0:
        raise ValueError("Off preference weight must be zero")
    for value in (table.off, table.low, table.high):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("Preference weights must be finite and nonnegative")


def _resolve_weight(name: str, level: PreferenceLevel, capability: PreferenceCapability,
                    table: PreferenceWeightTable) -> float:
    validate_weight_table(table)
    if level not in ("off", "low", "high"):
        raise ValueError(f"Invalid {name} preference level: {level}")
    if level not in capability.supported_levels:
        raise ValueError(f"Unsupported {name} preference '{level}': {capability.unavailable_reason}")
    weights = {"off": table.off, "low": table.low, "high": table.high}
    value = weights[level]
    if value is None:
        raise ValueError(f"Uncalibrated {name} preference level: {level}")
    return value


def resolve_preferences(settings: PreferenceSettings,
                        tables: PreferenceWeightTables = PreferenceWeightTables(), *,
                        hills: HillData | None = None) -> ResolvedPreferences:
    capabilities = preference_capabilities(hills)
    weights = PreferenceWeights(
        _resolve_weight("hills", settings.hills, capabilities.hills, tables.hills),
        _resolve_weight("signals", settings.signals, capabilities.signals, tables.signals),
        _resolve_weight("busy_roads", settings.busy_roads, capabilities.busy_roads, tables.busy_roads),
        _resolve_weight("greenery", settings.greenery, capabilities.greenery, tables.greenery),
    )
    return ResolvedPreferences(settings, weights, tables)

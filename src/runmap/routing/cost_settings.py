"""Validate preference capabilities and resolve independently versioned weights."""

import math
from runmap.environment_fields import EnvironmentField

from runmap.routing.cost_types import (
    PreferenceCapabilities, PreferenceCapability, PreferenceLevel, PreferenceSettings,
    PreferenceWeights, PreferenceWeightTable, PreferenceWeightTables, ResolvedPreferences,
)
from runmap.routing.hill_types import HillData, HillModel


COST_MODEL_VERSION = "distance-preferences-v1"
COST_UNITS = "equivalent_metres"


def preference_capabilities(hills: HillData | None = None) -> PreferenceCapabilities:
    """Advertise only implemented algorithms with verified feature support."""
    return prepared_preference_capabilities(hills.model if hills else None, hills.valid_fraction if hills else None)


def prepared_preference_capabilities(model: HillModel | None, valid_fraction: float | None, *, environment: bool = False) -> PreferenceCapabilities:
    return PreferenceCapabilities(
        (PreferenceCapability(("off", "low", "high"), model.version, valid_fraction, None)
         if model is not None else PreferenceCapability(("off",), None, None, "Prepared hill measurements are unavailable.")),
        PreferenceCapability(("off",), None, None, "Crossing passage attribution is not prepared."),
        PreferenceCapability(("off",), None, None, "Buffered road context is not prepared."),
        PreferenceCapability(("off",), None, None, "Buffered green-area context is not prepared."),
        **({f"environment_{field}": PreferenceCapability(("off", "low", "high"),
            "mapped-context-environment-cost-v1", None, None) for field in
            EnvironmentField} if environment else {}),
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
    return resolve_prepared_preferences(settings, capabilities, tables)


def resolve_prepared_preferences(settings: PreferenceSettings, capabilities: PreferenceCapabilities,
                                  tables: PreferenceWeightTables = PreferenceWeightTables()) -> ResolvedPreferences:
    weights = PreferenceWeights(
        _resolve_weight("hills", settings.hills, capabilities.hills, tables.hills),
        _resolve_weight("signals", settings.signals, capabilities.signals, tables.signals),
        _resolve_weight("busy_roads", settings.busy_roads, capabilities.busy_roads, tables.busy_roads),
        _resolve_weight("greenery", settings.greenery, capabilities.greenery, tables.greenery),
        **{f"environment_{field}": _resolve_weight(f"environment_{field}",
            getattr(settings, f"environment_{field}"), getattr(capabilities, f"environment_{field}"),
            getattr(tables, f"environment_{field}")) for field in EnvironmentField},
    )
    return ResolvedPreferences(settings, weights, tables)

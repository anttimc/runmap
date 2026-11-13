"""Select immutable hill, environment, and signal inputs for a routing request."""

from dataclasses import dataclass
import sqlite3
from time import monotonic

import numpy as np
from numpy.typing import NDArray

from runmap import _routing_native
from runmap.environment_fields import EnvironmentField
from runmap.paths.environment_join import read_environment_segments
from runmap.paths.numeric_hills import read_hill_intervals
from runmap.paths.routing_inputs import read_hill_payload
from runmap.routing.cost_types import PreferenceSettings
from runmap.routing.deadlines import check_deadline


@dataclass(frozen=True)
class PreparedRoutingInputs:
    numeric: sqlite3.Connection
    environment: sqlite3.Connection | None = None
    signals: sqlite3.Connection | None = None
    packed: sqlite3.Connection | None = None


@dataclass(frozen=True)
class InputRequirements:
    hills: bool = False
    environment: bool = False
    signals: bool = False


def search_input_requirements(preferences: PreferenceSettings) -> InputRequirements:
    return InputRequirements(hills=preferences.hills != "off", signals=preferences.signals != "off",
        environment=any(getattr(preferences, f"environment_{field}") != "off" for field in EnvironmentField))


def endpoint_hill_detail(inputs: PreparedRoutingInputs, segment: int) -> bytes | None:
    """Keep partial-segment detail independent of endpoint location data."""
    return (read_hill_payload(inputs.packed, segment) if inputs.packed is not None
            else read_hill_intervals(inputs.numeric, segment))


def route_environment_unknown(inputs: PreparedRoutingInputs, lengths: dict[int, float],
                              spans: list[tuple[int, float, float]], deadline: float | None
                              ) -> dict[EnvironmentField, NDArray[np.float64]]:
    """Keep zero-weight diagnostics proportional to the selected traversals."""
    if inputs.environment is None:
        raise ValueError("Environment inputs are unavailable")
    check_deadline(deadline)
    intervals = read_environment_segments(inputs.environment, list(lengths), deadline=deadline)
    check_deadline(deadline)
    unknown = _routing_native.environment_unknown_lengths(list(intervals.items()), list(lengths.items()), spans,
        timeout_seconds=None if deadline is None else max(0.0, deadline - monotonic()))
    if set(unknown) != {field.value for field in EnvironmentField}:
        raise ValueError("Python and native environment fields differ; rebuild the native extension")
    return {EnvironmentField(name): np.frombuffer(values, dtype="<f8") for name, values in unknown.items()}

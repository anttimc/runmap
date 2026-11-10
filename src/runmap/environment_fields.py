"""Named environment measurements and field semantics shared by preparation and routing."""

from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple


class EnvironmentField(StrEnum):
    GREENERY = "greenery"
    WATER = "water"
    TRAFFIC = "traffic"
    WETLAND = "wetland"


class EnvironmentValues(NamedTuple):
    greenery: float | None
    water: float | None
    traffic: float | None
    wetland: float | None


class EnvironmentSupport(NamedTuple):
    context_support: float | None
    traffic_context_support: float | None


class EnvironmentLengths(NamedTuple):
    greenery: float
    water: float
    traffic: float
    wetland: float


@dataclass(frozen=True)
class EnvironmentFieldDefinition:
    prefer_high: bool
    requires_traffic_support: bool = False


FIELD_DEFINITIONS = {
    EnvironmentField.GREENERY: EnvironmentFieldDefinition(prefer_high=True),
    EnvironmentField.WATER: EnvironmentFieldDefinition(prefer_high=True),
    EnvironmentField.TRAFFIC: EnvironmentFieldDefinition(prefer_high=False, requires_traffic_support=True),
    EnvironmentField.WETLAND: EnvironmentFieldDefinition(prefer_high=False),
}

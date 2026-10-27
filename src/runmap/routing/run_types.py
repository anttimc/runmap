"""Route modes and settings shared by native and reference planning."""

from dataclasses import dataclass
from typing import Literal


RunMode = Literal["one_way", "round_trip"]


@dataclass(frozen=True)
class RunSettings:
    avoidance_ratio: float = 0.18
    centre_enabled: bool = True
    radius_ratio: float = 0.15
    transition_ratio: float = 0.05
    side_strength: float = 2.0
    reuse_strength: float = 4.0

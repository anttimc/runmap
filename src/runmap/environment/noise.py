"""Effective sound-intensity attenuation from a configurable reference-car intensity."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class RoadNoiseConfig:
    reference_distance_m: float
    car_intensity_w_m2: float
    intensity_reference_w_m2: float
    discomfort_floor_db: float
    discomfort_full_db: float
    spreading_exponents: dict[str, float]


def source_intensity(distance_m: NDArray[np.float32] | NDArray[np.float64] | float, factor: float,
                     exponent: float, config: RoadNoiseConfig) -> NDArray[np.float64]:
    distance = np.maximum(np.asarray(distance_m, dtype=np.float64), config.reference_distance_m)
    return config.car_intensity_w_m2 * factor * (config.reference_distance_m / distance)**exponent


def intensity_to_db(intensity: NDArray[np.float64], config: RoadNoiseConfig) -> NDArray[np.float32]:
    with np.errstate(divide="ignore"):
        return (10 * (np.log10(intensity) - np.log10(config.intensity_reference_w_m2))).astype(np.float32)


def discomfort(level_db: NDArray[np.float32], config: RoadNoiseConfig) -> NDArray[np.float32]:
    return np.clip((level_db - config.discomfort_floor_db) /
                   (config.discomfort_full_db - config.discomfort_floor_db), 0, 1).astype(np.float32)


def audible_distance(factor: float, exponent: float, config: RoadNoiseConfig) -> float:
    floor_intensity = config.intensity_reference_w_m2 * 10**(config.discomfort_floor_db / 10)
    return config.reference_distance_m * (config.car_intensity_w_m2 * factor / floor_intensity)**(1 / exponent)

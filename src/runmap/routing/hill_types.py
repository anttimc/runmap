"""Prepared directional hill features retain physical coverage and provenance."""

from dataclasses import dataclass
from collections.abc import Mapping

from runmap.routing.types import GraphDocument


@dataclass(frozen=True)
class HillModel:
    version: str = "minetti-uphill-preference-v3"
    source_doi: str = "10.1152/japplphysiol.01177.2001"
    coefficients: tuple[float, ...] = (155.4, -30.4, -43.3, 46.3, 19.5, 3.6)
    fitted_grade_limit: float = 0.45
    outside_fit_policy: str = "boundary_tangent"
    uncertain_grade_percent: float = 20.0
    uphill_reference_grade_percent: float = 5.0
    uphill_grade_power: float = 2.0


@dataclass(frozen=True)
class HillFeatures:
    penalty_m: float = 0.0
    ascent_m: float = 0.0
    descent_m: float = 0.0
    valid_length_m: float = 0.0
    trusted_length_m: float = 0.0
    fallback_length_m: float = 0.0
    length_m: float = 0.0


@dataclass(frozen=True)
class HillInterval:
    graph_start_m: float
    graph_end_m: float
    forward: HillFeatures
    backward: HillFeatures


@dataclass(frozen=True)
class HillData:
    graph: GraphDocument
    graph_digest: str
    model: HillModel
    segments: Mapping[str, tuple[HillInterval, ...]]
    valid_fraction: float
    trusted_fraction: float

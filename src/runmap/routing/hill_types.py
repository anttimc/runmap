"""Prepared directional hill features retain physical coverage and provenance."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from runmap.routing.integer_graph import IntegerGraph
from runmap.routing.types import GraphDocument


@dataclass(frozen=True)
class HillModel:
    version: str = "minetti-excess-uphill-preference-v4"
    source_doi: str = "10.1152/japplphysiol.01177.2001"
    coefficients: tuple[float, ...] = (155.4, -30.4, -43.3, 46.3, 19.5, 3.6)
    fitted_grade_limit: float = 0.45
    outside_fit_policy: str = "boundary_tangent"
    uncertain_grade_percent: float = 20.0
    uphill_reference_grade_percent: float = 5.0
    uphill_grade_power: float = 2.0


@dataclass(frozen=True, slots=True)
class HillFeatures:
    penalty_m: float = 0.0
    ascent_m: float = 0.0
    descent_m: float = 0.0
    valid_length_m: float = 0.0
    trusted_length_m: float = 0.0
    fallback_length_m: float = 0.0
    length_m: float = 0.0


@dataclass(frozen=True, slots=True)
class HillInterval:
    """Transient preparation record; the retained store uses packed arrays."""

    graph_start_m: float
    graph_end_m: float
    forward: HillFeatures
    backward: HillFeatures


@dataclass(frozen=True)
class HillData:
    """Arrays share segment IDs; feature columns follow HillFeatures field order."""

    graph: GraphDocument
    graph_digest: str
    model: HillModel
    index: IntegerGraph
    interval_offsets: NDArray[np.int64]
    chainage: NDArray[np.float64]
    features: NDArray[np.float64]
    totals: NDArray[np.float64]
    valid_fraction: float
    trusted_fraction: float

"""Prepare dataset-wide features before selecting request-sized windows."""

from dataclasses import dataclass
from collections.abc import Sequence
from pathlib import Path

from runmap.paths.coverage import COVERAGE_VERSION, Coverage, graph_coverage, near_coverage_boundary
from runmap.paths.dead_ends import (ACCESS_POLICY, CLASSIFIER_VERSION, ENTRANCE_LIMIT_M,
                                   Classification, classify_building_access)
from runmap.paths.flat import FLAT_GRADE_THRESHOLDS, FLAT_MODEL_VERSION, MINIMUM_FLAT_LENGTH_M
from runmap.paths.measurements import MeasurementData, load_measurements
from runmap.routing.hill_types import HillData, HillFeatures, HillInterval, HillModel
from runmap.routing.hills import build_hill_data
from runmap.routing.profiles import POLICY_VERSION, exploration_profile
from runmap.routing.search import BOUNDARY_VERSION, GraphData, build_graph_index
from runmap.routing.types import Coordinate, EdgeProfile, GraphDocument, Measurement


FEATURE_VERSION = "prepared-path-features-v1"


@dataclass(frozen=True)
class PreparedModels:
    version: str
    flat_version: str
    flat_minimum_length_m: float
    flat_grade_thresholds: tuple[float, ...]
    coverage_version: str
    classifier_version: str
    access_policy: str
    entrance_limit_m: float
    policy_version: str
    boundary_version: str
    profiles: tuple[EdgeProfile, ...]
    hill_model: HillModel | None


@dataclass(frozen=True)
class PreparedNetwork:
    routing: GraphData
    measurements: MeasurementData | None
    hills: HillData | None
    coverage: Coverage | None
    building_access: dict[str, Classification]


@dataclass(frozen=True)
class PreparedPolicy:
    policy_id: str
    forward: bool
    backward: bool
    reason: str | None


@dataclass(frozen=True)
class PreparedSegment:
    segment_id: str
    surface_class: str
    boundary_blocked: bool
    building_access: Classification | None
    projected_geometry: tuple[tuple[float, float], ...]
    policies: tuple[PreparedPolicy, ...]
    hill_intervals: tuple[HillInterval, ...]
    forward_hills: HillFeatures | None
    backward_hills: HillFeatures | None


@dataclass(frozen=True)
class PreparedDisplayInterval:
    measurement: Measurement
    surface_class: str
    flat_min_grade_50m: float | None
    coverage_limited: bool
    building_access: Classification | None


def prepared_models(hill_model: HillModel | None) -> PreparedModels:
    return PreparedModels(
        FEATURE_VERSION, FLAT_MODEL_VERSION, MINIMUM_FLAT_LENGTH_M, FLAT_GRADE_THRESHOLDS,
        COVERAGE_VERSION, CLASSIFIER_VERSION, ACCESS_POLICY, ENTRANCE_LIMIT_M,
        POLICY_VERSION, BOUNDARY_VERSION,
        tuple(exploration_profile(surface, avoid_stairs)
              for surface in ("all", "surfaced") for avoid_stairs in (False, True)),
        hill_model,
    )


def prepare_network(graph: GraphDocument, graph_path: Path) -> PreparedNetwork:
    routing = build_graph_index(graph)
    measurements = load_measurements(graph_path, graph=graph, index=routing.integer) if "measurements" in graph else None
    coverage = graph_coverage(graph) if graph["segments"] else None
    building_access = classify_building_access(
        routing, lambda geometry: _near_boundary(coverage, geometry),
        coverage is not None and coverage["kind"] == "prepared_extract")
    hills = build_hill_data(routing, measurements) if measurements is not None else None
    return PreparedNetwork(routing, measurements, hills, coverage, building_access)


def _near_boundary(coverage: Coverage | None, geometry: Sequence[Coordinate]) -> bool:
    return coverage is None or near_coverage_boundary(coverage, geometry)

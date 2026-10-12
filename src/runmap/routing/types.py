"""Shared routing schemas preserve the prepared graph's JSON representation."""

from typing import NotRequired, TypedDict


Coordinate = list[float] | tuple[float, float]
Bounds = tuple[float, float, float, float]
Tags = dict[str, str]


class Node(TypedDict):
    id: str
    longitude: float
    latitude: float
    barrier: NotRequired[str]
    access: NotRequired[str]
    foot: NotRequired[str]
    entrance: NotRequired[str]
    crossing: NotRequired[str]
    highway: NotRequired[str]


Segment = TypedDict("Segment", {
    "id": str, "osm_way_id": str, "from": str, "to": str,
    "length_m": float, "geometry": list[Coordinate], "tags": Tags,
    "forward": NotRequired[bool], "backward": NotRequired[bool],
    "ascent_m": NotRequired[float], "descent_m": NotRequired[float],
    "terrain_valid_fraction": NotRequired[float],
    "terrain_sample_count": NotRequired[int],
    "terrain_totals_complete": NotRequired[bool],
})

Arc = TypedDict("Arc", {
    "id": str, "segment_id": str, "from": str, "to": str,
    "length_m": float, "ascent_m": NotRequired[float],
    "descent_m": NotRequired[float], "terrain_valid_fraction": NotRequired[float],
})


class Boundary(TypedDict):
    id: str
    osm_way_id: str
    ref: str
    name: str
    geometry: list[Coordinate]


class OsmSource(TypedDict, total=False):
    bounds_wgs84: list[float]
    source_path: str
    sha256: str
    bytes: int


class GraphSource(TypedDict, total=False):
    osm: OsmSource
    experiment_id: str
    experiment_config_digest: str


class GraphSummary(TypedDict):
    source_node_count: int
    source_way_count: int
    selected_way_count: int
    rejected_way_counts: dict[str, int]
    node_count: int
    segment_count: int
    arc_count: int
    physical_length_km: float
    component_count: int
    largest_component_nodes: int
    highway_segment_counts: dict[str, int]
    missing_surface_segments: int
    bridge_segments: int
    tunnel_segments: int
    stair_segments: int
    pedestrian_area_boundary_segments: int
    pedestrian_oneway_segments: int
    barrier_nodes: int
    route_boundary_way_count: int
    terrain_valid_fraction: NotRequired[float]


class GraphDocument(TypedDict):
    nodes: list[Node]
    segments: list[Segment]
    arcs: list[Arc]
    schema_version: NotRequired[int]
    source: NotRequired[GraphSource]
    route_boundaries: NotRequired[list[Boundary]]
    summary: NotRequired[GraphSummary]


class EdgeProfile(TypedDict, total=False):
    id: str
    forbidden_access_values: list[str]
    forbidden_highways: list[str]
    forbidden_surfaces: list[str]
    forbid_unknown_surface_highways: list[str]
    surface_preference: str


class RouteMetrics(TypedDict):
    distance_m: float
    ascent_m: float
    descent_m: float
    repeated_distance_m: float
    repeated_fraction: float
    stair_distance_m: float
    immediate_reversals: int
    start_access_reversal: bool
    start_access_repeated_distance_m: float
    arc_count: int
    distinct_segment_count: int

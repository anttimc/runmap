"""Validated configuration for bounded route-planning experiments."""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re


EXPERIMENT_SCHEMA_VERSION = 1
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_OSM_REF = re.compile(r"^[A-Za-z0-9-]+$")


@dataclass(frozen=True)
class StartLocation:
    name: str
    address: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class RoutingExperiment:
    schema_version: int
    id: str
    title: str
    bounds: tuple
    start: StartLocation
    request: dict
    terrain: dict
    baseline: dict
    route_boundaries: tuple
    topology: dict
    area_note: str
    config_digest: str


def load_experiment(path):
    """Load one strictly validated routing-experiment definition."""
    path = Path(path)
    document = json.loads(path.read_text())
    required = {
        "schema_version", "id", "title", "bounds_epsg3067", "start",
        "request", "terrain", "baseline", "route_boundaries", "topology",
    }
    missing = required - document.keys()
    if missing:
        raise ValueError(f"Routing experiment is missing: {', '.join(sorted(missing))}")
    if document["schema_version"] != EXPERIMENT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported routing experiment schema {document['schema_version']}")
    experiment_id = document["id"]
    if not isinstance(experiment_id, str) or not _ID.fullmatch(experiment_id):
        raise ValueError("Routing experiment id must be a lowercase hyphenated slug")
    if path.stem != experiment_id:
        raise ValueError("Routing experiment filename must match its id")
    raw_bounds = document["bounds_epsg3067"]
    if not isinstance(raw_bounds, list) or len(raw_bounds) != 4:
        raise ValueError("bounds_epsg3067 must contain west, south, east, north")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in raw_bounds
    ):
        raise ValueError("Routing experiment bounds must be finite numbers")
    bounds = tuple(float(value) for value in raw_bounds)
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError("Routing experiment bounds must have positive area")
    start = document["start"]
    if not isinstance(start, dict):
        raise ValueError("start must be an object")
    for key in ("name", "address", "latitude", "longitude"):
        if key not in start:
            raise ValueError(f"start is missing {key}")
    latitude = start["latitude"]
    longitude = start["longitude"]
    if not isinstance(latitude, (int, float)) or not -90 <= latitude <= 90:
        raise ValueError("start.latitude must be from -90 to 90")
    if not isinstance(longitude, (int, float)) or not -180 <= longitude <= 180:
        raise ValueError("start.longitude must be from -180 to 180")
    request = document["request"]
    if not isinstance(request, dict) or request.get("mode") not in {
        "environment-loop", "destination-outing"
    }:
        raise ValueError("request.mode is unsupported")
    terrain = document["terrain"]
    if not isinstance(terrain, dict):
        raise ValueError("terrain must be an object")
    if not isinstance(terrain.get("elevation_region"), str):
        raise ValueError("terrain.elevation_region must be a region id")
    if not isinstance(terrain.get("wavelength_m"), int) or terrain["wavelength_m"] < 0:
        raise ValueError("terrain.wavelength_m must be a non-negative integer")
    if not isinstance(terrain.get("sampling_interval_m"), (int, float)) or terrain["sampling_interval_m"] <= 0:
        raise ValueError("terrain.sampling_interval_m must be positive")
    baseline = document["baseline"]
    if not isinstance(baseline, dict):
        raise ValueError("baseline must be an object")
    for name in ("anchor_count", "alternative_count"):
        if not isinstance(baseline.get(name), int) or baseline[name] < 1:
            raise ValueError(f"baseline.{name} must be a positive integer")
    for name in ("ascent_costs_per_m", "overlap_factors"):
        values = baseline.get(name)
        if not isinstance(values, list) or not values or any(
            not isinstance(value, (int, float)) or value < 0 for value in values
        ):
            raise ValueError(f"baseline.{name} must contain non-negative numbers")
    if any(value < 1 for value in baseline["overlap_factors"]):
        raise ValueError("baseline.overlap_factors must be at least one")
    if not isinstance(baseline.get("stairs_multiplier"), (int, float)) or baseline["stairs_multiplier"] < 1:
        raise ValueError("baseline.stairs_multiplier must be at least one")
    route_boundaries = document["route_boundaries"]
    if not isinstance(route_boundaries, list):
        raise ValueError("route_boundaries must be a list")
    for boundary in route_boundaries:
        if not isinstance(boundary, dict) or not isinstance(boundary.get("osm_ref"), str):
            raise ValueError("Each route boundary must have an OSM ref")
        if not _OSM_REF.fullmatch(boundary["osm_ref"]):
            raise ValueError("Route-boundary OSM refs must be simple identifiers")
        if boundary.get("behavior") != "do-not-cross":
            raise ValueError("Unsupported route-boundary behavior")
    topology = document["topology"]
    if not isinstance(topology, dict):
        raise ValueError("topology must be an object")
    face_bounds = topology.get("face_test_bounds_epsg3067")
    if not isinstance(face_bounds, list) or len(face_bounds) != 4 or any(
        not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in face_bounds
    ):
        raise ValueError("topology.face_test_bounds_epsg3067 must contain four numbers")
    if face_bounds[2] <= face_bounds[0] or face_bounds[3] <= face_bounds[1]:
        raise ValueError("Topology face-test bounds must have positive area")
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return RoutingExperiment(
        schema_version=document["schema_version"],
        id=experiment_id,
        title=str(document["title"]),
        bounds=bounds,
        start=StartLocation(
            name=str(start["name"]),
            address=str(start["address"]),
            latitude=float(latitude),
            longitude=float(longitude),
        ),
        request=dict(request),
        terrain=dict(terrain),
        baseline=dict(baseline),
        route_boundaries=tuple(dict(boundary) for boundary in route_boundaries),
        topology=dict(topology),
        area_note=str(document.get("area_note", "")),
        config_digest=hashlib.sha256(canonical).hexdigest(),
    )


def find_experiment(directory, experiment_id):
    if not isinstance(experiment_id, str) or not _ID.fullmatch(experiment_id):
        raise ValueError("Invalid routing experiment id")
    return load_experiment(Path(directory) / f"{experiment_id}.json")

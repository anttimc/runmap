"""Acquire inspectable OSM context through OSMnx, independently of routing."""

import argparse
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

from rasterio.warp import transform_bounds


Bounds = tuple[float, float, float, float]
Tags = dict[str, bool | list[str]]


@dataclass(frozen=True)
class EnvironmentRequest:
    schema_version: int
    id: str
    bounds_epsg3067: Bounds
    halo_m: float
    tags: Tags


def load_request(path: Path) -> EnvironmentRequest:
    """Validate a bounded pilot before creating files or contacting a provider."""
    document = json.loads(path.read_text())
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported environment request schema")
    identifier = document.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier):
        raise ValueError("Environment id must be a lowercase hyphenated slug")
    values = document.get("bounds_epsg3067")
    if not isinstance(values, list) or len(values) != 4:
        raise ValueError("Expected four EPSG:3067 bounds")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError("Bounds must be finite numbers")
    west, south, east, north = (float(v) for v in values)
    halo = document.get("halo_m")
    if isinstance(halo, bool) or not isinstance(halo, (int, float)) or not math.isfinite(halo) or halo < 300:
        raise ValueError("Environment halo must be finite and at least 300 m")
    if east <= west or north <= south or max(east - west, north - south) + 2 * halo > 10000:
        raise ValueError("Pilot bounds plus halo must have positive area and span at most 10 km")
    tags = document.get("tags")
    if not isinstance(tags, dict) or not tags:
        raise ValueError("Expected nonempty OSM tag selectors")
    for key, value in tags.items():
        if not isinstance(key, str) or not key:
            raise ValueError("OSM tag keys must be nonempty strings")
        if value is not True and not (
            isinstance(value, list) and value and all(isinstance(v, str) and v for v in value)
        ):
            raise ValueError("OSM selectors must be true or nonempty lists of strings")
    return EnvironmentRequest(1, identifier, (west, south, east, north), float(halo), tags)


def query_bounds(request: EnvironmentRequest) -> Bounds:
    """Densify the projected boundary before finding a conservative WGS84 box."""
    west, south, east, north = request.bounds_epsg3067
    halo = request.halo_m
    return transform_bounds(
        "EPSG:3067", "EPSG:4326", west - halo, south - halo,
        east + halo, north + halo, densify_pts=21,
    )


def file_identity(path: Path) -> dict[str, str | int]:
    payload = path.read_bytes()
    return {"name": path.name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _write_json(path: Path, document: object) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _prepare_directory(request: EnvironmentRequest, destination: Path) -> None:
    if (destination / "manifest.json").exists():
        raise FileExistsError(f"Completed acquisition already exists: {destination}")
    record = asdict(request)
    record["bounds_epsg3067"] = list(request.bounds_epsg3067)
    request_path = destination / "request.json"
    if request_path.exists():
        if json.loads(request_path.read_text()) != record:
            raise ValueError("Existing acquisition has different bounds, halo, or tags")
    elif destination.exists() and any(destination.iterdir()):
        raise ValueError("Refusing to reuse a nonempty directory without a matching request")
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(request_path, record)


def acquire_environment(request: EnvironmentRequest, destination: Path) -> Path:
    """Publish source features and provenance; interrupted runs reuse their cache."""
    import osmnx as ox

    _prepare_directory(request, destination)
    setting_names = ("cache_folder", "use_cache", "log_console", "requests_timeout")
    previous = {name: getattr(ox.settings, name) for name in setting_names}
    started = datetime.now(timezone.utc).isoformat()
    bounds = query_bounds(request)
    try:
        ox.settings.cache_folder = destination / "raw-overpass"
        ox.settings.use_cache = True
        ox.settings.log_console = False
        ox.settings.requests_timeout = 180
        features = ox.features.features_from_bbox(bounds, request.tags)
    finally:
        for name, value in previous.items():
            setattr(ox.settings, name, value)

    raw_paths = sorted((destination / "raw-overpass").glob("*.json"))
    if not raw_paths:
        raise ValueError("No raw Overpass responses retained; refusing to publish")
    source_times: set[str] = set()
    for path in raw_paths:
        document = json.loads(path.read_text())
        if document.get("remark") or not isinstance(document.get("elements"), list):
            raise ValueError(f"Incomplete or invalid Overpass response: {path}")
        timestamp = document.get("osm3s", {}).get("timestamp_osm_base")
        if isinstance(timestamp, str):
            source_times.add(timestamp)

    features = features.rename_axis(["osm_type", "osm_id"]).reset_index()
    output = destination / "features.geojson"
    temporary = output.with_suffix(".geojson.part")
    temporary.write_text(features.to_json(
        drop_id=True, to_wgs84=True, na="drop", separators=(",", ":"),
    ))
    temporary.replace(output)
    manifest = {
        "schema_version": 1,
        "status": "pilot-source-acquisition",
        "request": asdict(request),
        "query_bbox_wgs84_left_bottom_right_top": bounds,
        "acquisition_started_at": started,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "osm_base_timestamps": sorted(source_times),
        "provider": "OpenStreetMap contributors via Overpass / OSMnx",
        "overpass_url": ox.settings.overpass_url,
        "overpass_settings": ox.settings.overpass_settings,
        "osmnx_version": ox.__version__,
        "licence": "ODbL 1.0",
        "licence_url": "https://www.openstreetmap.org/copyright",
        "feature_crs": "EPSG:4326",
        "analysis_crs": "EPSG:3067",
        "missing_tag_properties": "omitted",
        "feature_count": len(features),
        "geometry_types": {str(k): int(v) for k, v in features.geom_type.value_counts().items()},
        "invalid_geometry_count": int((~features.geometry.is_valid).sum()),
        "features": file_identity(output),
        "raw_responses": [file_identity(path) for path in raw_paths],
        "limitations": [
            "BBox queries can miss enclosing polygons with no selected member vertices.",
            "Geometry assembly and mapping completeness need independent review.",
            "No raster fields, training labels, or routing preferences are prepared.",
            "Cached responses retain their OSM base timestamps; publication is not source freshness.",
        ],
    }
    _write_json(destination / "manifest.json", manifest)
    return destination / "manifest.json"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    request = load_request(args.config)
    destination = args.output or Path("data/environment/raw") / request.id
    if args.dry_run:
        print(json.dumps({"request": asdict(request), "query_bbox_wgs84": query_bounds(request),
                          "output": str(destination)}, indent=2))
        return
    print(f"Acquiring {request.id}: OSM features and raw responses into {destination}", flush=True)
    print(acquire_environment(request, destination))


if __name__ == "__main__":
    main()

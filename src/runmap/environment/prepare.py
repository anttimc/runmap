"""Prepare a bounded first-version environment raster bundle from a frozen OSM pilot."""

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from importlib.metadata import version
from pathlib import Path
import platform
from time import perf_counter
from collections.abc import Sequence

import numpy as np
import rasterio
from rasterio.transform import array_bounds

from runmap.environment.fields import BaselineConfig, FloatGrid, build_fields, make_grid, Grid
from runmap.environment.objects import load_objects, evidence_document, road_class
from runmap.environment.preview import write_previews
from runmap.environment.noise import RoadNoiseConfig
from runmap.sources.environment import file_identity


DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config/environment/baseline-v3.json"
NODATA = -9999.0


def load_config(path: Path) -> BaselineConfig:
    document = json.loads(path.read_text())
    noise = document.pop("road_noise", None)
    config = BaselineConfig(**document, road_noise=RoadNoiseConfig(**noise) if noise is not None else None)
    if config.schema_version not in (1, 2, 3) or config.resolution_m != 10 or config.coverage_resolution_m != 2:
        raise ValueError("Baseline requires schema 1, 2 or 3, a 10 m grid and 2 m coverage samples")
    if (config.schema_version >= 2) != (config.road_noise is not None):
        raise ValueError("Schemas 2 and 3 require road_noise configuration")
    for weight in (config.park_greenery_weight, config.reserve_greenery_weight):
        if isinstance(weight, bool) or not isinstance(weight, (float, int)) or not 0 <= weight < 1:
            raise ValueError("Park and reserve greenery weights must be finite in [0, 1), below woodland weight 1")
    if not isinstance(config.include_all_water_polygons, bool):
        raise ValueError("include_all_water_polygons must be boolean")
    for radius in (config.forest_radius_m, config.water_radius_m, config.road_radius_m):
        if isinstance(radius, bool) or not isinstance(radius, int) or radius < 10 or radius > 500 or radius % 10:
            raise ValueError("Context radii must be multiples of 10 m between 10 and 500 m")
    allowed = {"motorway", "trunk", "primary", "secondary", "tertiary",
               "residential", "unclassified", "living_street", "service"}
    if not config.road_weights or not set(config.road_weights) <= allowed:
        raise ValueError("Unsupported motor-road class weights")
    for weight in config.road_weights.values():
        if isinstance(weight, bool) or not isinstance(weight, (float, int)) or not 0 <= weight <= 1:
            raise ValueError("Road weights must be finite numbers in [0, 1]")
    if config.road_noise is not None:
        validate_noise_config(config.road_noise, set(config.road_weights))
    return config


def validate_noise_config(noise: RoadNoiseConfig, road_classes: set[str]) -> None:
    positive = (noise.reference_distance_m, noise.car_intensity_w_m2, noise.intensity_reference_w_m2)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in positive):
        raise ValueError("Noise reference distance and intensities must be finite positive numbers")
    levels = (noise.discomfort_floor_db, noise.discomfort_full_db)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in levels):
        raise ValueError("Noise display levels must be finite numbers")
    if not 0 <= noise.discomfort_floor_db < noise.discomfort_full_db <= 200:
        raise ValueError("Noise display levels must increase within [0, 200] dB")
    if set(noise.spreading_exponents) != road_classes or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or not 1 <= value <= 2 for value in noise.spreading_exponents.values()):
        raise ValueError("Every road class requires a finite intensity attenuation exponent in [1, 2]")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_raster(path: Path, fields: dict[str, FloatGrid], grid: Grid) -> None:
    with rasterio.open(path, "w", driver="GTiff", width=grid.width, height=grid.height,
                       count=len(fields), dtype="float32", crs="EPSG:3067", transform=grid.transform,
                       nodata=NODATA, compress="deflate", predictor=3, tiled=True) as dataset:
        for band, (name, values) in enumerate(fields.items(), 1):
            dataset.write(np.where(np.isfinite(values), values, NODATA).astype(np.float32), band)
            dataset.set_band_description(band, name)


def implementation_identity() -> str:
    files = [Path(__file__).with_name(name) for name in ("objects.py", "fields.py", "noise.py", "prepare.py", "preview.py")]
    return hashlib.sha256(b"".join(path.read_bytes() for path in files)).hexdigest()


def prepare(source: Path, config_path: Path, destination: Path) -> Path:
    """Publish a completed bundle last; refuse replacement of any previous run."""
    started = perf_counter()
    config = load_config(config_path)
    source_manifest = json.loads((source / "manifest.json").read_text())
    source_manifest_identity = file_identity(source / "manifest.json")
    identity = file_identity(source / "features.geojson")
    if identity != source_manifest["features"]:
        raise ValueError("Source export does not match its acquisition manifest")
    request = source_manifest["request"]
    halo = request["halo_m"]
    if halo < max(config.forest_radius_m, config.water_radius_m, config.road_radius_m):
        raise ValueError("Source halo is smaller than required field context")
    core = request["bounds_epsg3067"]
    west, south, east, north = core
    bounds = [west - halo, south - halo, east + halo, north + halo]
    grid = make_grid(bounds, config.resolution_m)
    if grid.width <= 0 or grid.height <= 0 or grid.width * grid.height > 1_000_000:
        raise ValueError("Baseline v1 is limited to one million context cells")
    core_grid = make_grid(core, config.resolution_m)
    offset = int(halo / config.resolution_m)
    if halo % config.resolution_m:
        raise ValueError("Halo must align to the grid")
    destination.mkdir(parents=True, exist_ok=False)
    objects, invalid = load_objects(source / "features.geojson")
    objects = [obj for obj in objects if set(obj.categories) - {"road"} or road_class(obj) in config.road_weights]
    fields = build_fields(objects, grid, config)
    fields = {name: values[offset:offset + core_grid.height, offset:offset + core_grid.width]
              for name, values in fields.items()}
    write_raster(destination / "fields.tif", fields, core_grid)
    write_json(destination / "evidence.json", evidence_document(objects))
    previews = write_previews(destination, fields, core_grid)
    implementation = implementation_identity()
    dependencies = {name: version(name) for name in ("numpy", "scipy", "rasterio", "shapely", "Pillow")}
    dataset_id = hashlib.sha256(json.dumps({"config": asdict(config), "source": identity,
                                           "source_manifest": source_manifest_identity,
                                           "implementation": implementation, "dependencies": dependencies}, sort_keys=True).encode()).hexdigest()
    manifest = {
        "schema_version": 1, "method": f"mapped-context-baseline-v{config.schema_version}", "dataset_id": dataset_id,
        "published_at": datetime.now(timezone.utc).isoformat(), "source_features": identity,
        "source_manifest": source_manifest_identity,
        "source_osm_base_timestamps": source_manifest["osm_base_timestamps"],
        "config": asdict(config), "implementation_sha256": implementation,
        "dependencies": dependencies, "python_version": platform.python_version(),
        "platform": platform.platform(),
        "bounds_epsg3067": list(array_bounds(core_grid.height, core_grid.width, core_grid.transform)),
        "grid_shape": [core_grid.height, core_grid.width], "bands": list(fields),
        "previews": previews, "invalid_objects_skipped": invalid,
        "object_categories": dict(Counter(category for obj in objects for category in obj.categories)),
        "preparation_seconds": perf_counter() - started,
        "artifacts": [file_identity(path) for path in sorted(destination.iterdir()) if path.is_file()],
        "limitations": [
            "Mapped context only: mapping completeness is unverified for every field.",
            ("Greenery includes weaker within-polygon park/reserve contributions; this does not establish woodland cover."
             if config.schema_version >= 3 else "Parks/reserves without mapped woodland are evidence gaps, not automatically forest or nonforest."),
            "Park coverage includes leisure=park polygons only; nature-reserve designation does not imply park or forest cover.",
            ("Water proximity uses all mapped water-polygon boundaries and coastline; line waterways remain excluded."
             if config.include_all_water_polygons else "Water uses explicitly tagged lakes and coastline; other water polygons are unresolved/excluded candidates."),
            "Line streams and ditches do not contribute to water proximity. Coastline lines do not establish a sea interior.",
            ("Traffic uses an assumed reference-car intensity and effective attenuation exponents; it is not calibrated noise, dBA, or measured traffic."
             if config.road_noise else "Traffic uses linear class weights and distance only."),
            "Road classes and attenuation exponents supply uncalibrated priors; actual speed, flow, ground, shielding and atmospheric conditions are not modeled.",
            "Traffic retains only the dominant modeled road class, not summed acoustic energy from all traffic.",
            ("Inverse-distance noise has no 300 m cutoff. Traffic context support flags where an exterior source could dominate under the configured source-strength limits."
             if config.road_noise else "Linear traffic exposure is truncated at the configured road radius."),
            "Wetland presence does not determine path wetness, accessibility, or boardwalk condition.",
            "Coverage uses 2 m center samples; distances use occupied 10 m cells and can differ from exact geometry by roughly a cell diagonal.",
            "Evidence-gap flags identify known candidates only; their absence never certifies complete mapping.",
            "For v3, candidate hatching marks limited road context; parks/reserves and non-lake water polygons are accepted inputs.",
            "No learned model or routing preferences are enabled.",
        ],
    }
    write_json(destination / "manifest.json", manifest)
    return destination / "manifest.json"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=Path("data/environment/prepared/leppavaara-baseline-v3"))
    args = parser.parse_args(argv)
    print(prepare(args.source, args.config, args.output))


if __name__ == "__main__":
    main()

"""Prepare regional display fields in haloed blocks without region-sized sample arrays."""

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window
from shapely.geometry import box
from shapely.strtree import STRtree

from runmap.environment.fields import BaselineConfig, FloatGrid, build_fields, make_grid
from runmap.environment.noise import audible_distance
from runmap.environment.objects import EnvironmentObject, load_objects, road_class
from runmap.environment.prepare import DEFAULT_CONFIG, NODATA, implementation_identity, load_config, write_json
from runmap.sources.environment import file_identity, load_request


BLOCK_M = 4000


def context_radius(config: BaselineConfig) -> int:
    """Include every road that could exceed the discomfort floor, plus raster error."""
    acoustic = max((audible_distance(weight, config.road_noise.spreading_exponents[kind], config.road_noise)
                    for kind, weight in config.road_weights.items()), default=0) if config.road_noise else 0
    radius = max(config.forest_radius_m, config.water_radius_m, config.road_radius_m, acoustic)
    return int(np.ceil((radius + 3 * config.resolution_m) / config.resolution_m)) * config.resolution_m


def block_fields(objects: list[EnvironmentObject], bounds: list[float], config: BaselineConfig,
                 halo: int) -> dict[str, FloatGrid]:
    west, south, east, north = bounds
    grid = make_grid([west - halo, south - halo, east + halo, north + halo], config.resolution_m)
    fields = build_fields(objects, grid, config)
    offset = halo // config.resolution_m
    height = int((north - south) / config.resolution_m)
    width = int((east - west) / config.resolution_m)
    fields = {name: values[offset:offset + height, offset:offset + width].copy()
              for name, values in fields.items()}
    # These diagnostic channels otherwise imply unlimited distance/noise knowledge
    # from a bounded block. Scores retain their original finite influence ranges.
    for name in ("lake_distance_m", "sea_distance_m", "water_polygon_distance_m"):
        if name in fields:
            fields[name][fields[name] > config.water_radius_m] = np.nan
    if config.road_noise:
        fields["traffic_noise_db"][fields["traffic_noise_db"] < config.road_noise.discomfort_floor_db] = np.nan
    return fields


def write_regional_raster(path: Path, bounds: list[float], objects: list[EnvironmentObject],
                          config: BaselineConfig, halo: int, block_m: int) -> list[str]:
    """Persist one bounded block at a time on the common metric grid."""
    grid = make_grid(bounds, config.resolution_m)
    tree = STRtree([obj.geometry for obj in objects])
    band_count = len(build_fields([], make_grid([0, 0, 10, 10], config.resolution_m), config))
    bands: list[str] = []
    west, south, east, north = bounds
    with rasterio.open(path, "w", driver="GTiff", width=grid.width,
                       height=grid.height, count=band_count, dtype="float32", crs="EPSG:3067",
                       transform=grid.transform, nodata=NODATA, compress="deflate", predictor=3,
                       tiled=True, BIGTIFF="IF_SAFER") as raster:
        for top in range(int(north), int(south), -block_m):
            for left in range(int(west), int(east), block_m):
                bottom, right = max(south, top - block_m), min(east, left + block_m)
                positions = tree.query(box(left - halo, bottom - halo, right + halo, top + halo))
                selected = [objects[int(position)] for position in positions]
                fields = block_fields(selected, [left, bottom, right, top], config, halo)
                if not bands:
                    bands = list(fields)
                    if len(bands) != raster.count:
                        raise ValueError("Regional band schema changed")
                    for index, name in enumerate(bands, 1):
                        raster.set_band_description(index, name)
                window = Window((left - west) / 10, (north - top) / 10, (right - left) / 10, (top - bottom) / 10)
                for index, values in enumerate(fields.values(), 1):
                    raster.write(np.where(np.isfinite(values), values, NODATA).astype(np.float32), index, window=window)
            print(f"Prepared northing {top}: {min(100, int((north - top + block_m) / (north - south) * 100))}%", flush=True)
        levels = [level for level in (2, 4, 8, 16, 32, 64) if level <= min(grid.width, grid.height)]
        raster.build_overviews(levels, Resampling.nearest)
        raster.update_tags(ns="rio_overview", resampling="nearest")
    return bands


def prepare_regional(source: Path, config_path: Path, destination: Path, block_m: int = BLOCK_M) -> Path:
    """Write analytical windows incrementally and publish the completion manifest last."""
    started = perf_counter()
    config = load_config(config_path)
    if config.schema_version != 3:
        raise ValueError("Regional preparation requires baseline v3")
    if block_m < 100 or block_m > BLOCK_M or block_m % config.resolution_m:
        raise ValueError("Block size must align to 10 m and be between 100 and 4000 m")
    if destination.exists():
        raise FileExistsError(f"Output directory already exists: {destination}")
    request = load_request(source / "request.json")
    source_manifest = json.loads((source / "manifest.json").read_text())
    identity = file_identity(source / "features.geojson")
    if identity != source_manifest["features"] or json.loads(json.dumps(asdict(request))) != source_manifest["request"]:
        raise ValueError("Source features or request do not match acquisition manifest")
    halo = context_radius(config)
    if request.halo_m < halo:
        raise ValueError(f"Regional source requires at least {halo} m halo")
    bounds = list(request.bounds_epsg3067)
    grid = make_grid(bounds, config.resolution_m)
    objects, invalid = load_objects(source / "features.geojson")
    objects = [obj for obj in objects if set(obj.categories) - {"road"} or road_class(obj) in config.road_weights]
    destination.mkdir(parents=True, exist_ok=False)
    bands = write_regional_raster(destination / "fields.tif", bounds, objects, config, halo, block_m)
    implementation = hashlib.sha256((implementation_identity() + Path(__file__).read_text()).encode()).hexdigest()
    manifest_identity = file_identity(source / "manifest.json")
    dependencies = {name: version(name) for name in ("numpy", "scipy", "rasterio", "shapely", "Pillow")}
    identity_inputs = {"source": identity, "source_manifest": manifest_identity, "config": asdict(config),
                       "implementation": implementation, "dependencies": dependencies,
                       "block_m": block_m, "halo_m": halo}
    manifest = {
        "schema_version": 2, "method": "mapped-context-regional-v3",
        "dataset_id": hashlib.sha256(json.dumps(identity_inputs, sort_keys=True).encode()).hexdigest(),
        "published_at": datetime.now(timezone.utc).isoformat(), "config": asdict(config),
        "source_features": identity, "source_manifest": manifest_identity,
        "source_osm_base_timestamps": source_manifest["osm_base_timestamps"],
        "bounds_epsg3067": bounds, "grid_shape": [grid.height, grid.width], "bands": bands,
        "block_m": block_m, "halo_m": halo, "implementation_sha256": implementation,
        "dependencies": dependencies,
        "invalid_objects_skipped": invalid,
        "object_categories": dict(Counter(category for obj in objects for category in obj.categories)),
        "preparation_seconds": perf_counter() - started,
        "artifacts": [file_identity(destination / "fields.tif")],
        "limitations": ["Mapped OSM context only; real-world mapping completeness is unverified.",
                        "Traffic is an uncalibrated dominant-road proxy, not measured noise.",
                        "Distances beyond the water influence radius and noise below the display floor are unavailable.",
                        "Enclosing OSM polygons can be missed by bbox acquisition; line waterways are excluded.",
                        "No environment routing costs are enabled."],
    }
    write_json(destination / "manifest.json", manifest)
    return destination / "manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=Path("data/environment/prepared/helsinki-capital-area-v3"))
    args = parser.parse_args()
    print(prepare_regional(args.source, args.config, args.output))


if __name__ == "__main__":
    main()

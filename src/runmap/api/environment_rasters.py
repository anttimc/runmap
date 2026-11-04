"""Serve bounded, prepared environment previews and numerical cell inspection."""

from dataclasses import dataclass
from functools import lru_cache
import json
import math
import os
from pathlib import Path
from typing import TypedDict, NotRequired

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
import rasterio
from rasterio.warp import transform
from shapely.geometry import box, Point, shape
from shapely.strtree import STRtree

from runmap.api.environment import read_manifest, source_directory
from runmap.environment.objects import EnvironmentObject, JsonValue, road_class
from runmap.sources.environment import file_identity
from runmap.environment.noise import RoadNoiseConfig, audible_distance, source_intensity, intensity_to_db, discomfort


router = APIRouter(prefix="/api/environment/rasters")
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[3] / "data/environment/prepared/leppavaara-baseline-v3"
LAYERS = {"forest", "greenery", "park", "water", "traffic", "wetland", "evidence_gap"}


class NoiseRules(TypedDict):
    reference_distance_m: float
    car_intensity_w_m2: float
    intensity_reference_w_m2: float
    discomfort_floor_db: float
    discomfort_full_db: float
    spreading_exponents: dict[str, float]


class RasterRules(TypedDict):
    schema_version: int
    road_radius_m: float
    forest_radius_m: float
    water_radius_m: float
    road_weights: dict[str, float]
    road_noise: NotRequired[NoiseRules | None]
    include_all_water_polygons: NotRequired[bool]


class RasterManifest(TypedDict):
    dataset_id: str
    source_features: dict[str, str | int]
    source_manifest: dict[str, str | int]
    config: RasterRules


class EvidenceItem(TypedDict):
    id: str
    distance_m: float
    category: str
    tags: dict[str, JsonValue]
    influence_at_exact_distance: NotRequired[float]
    modeled_noise_db: NotRequired[float]
    spreading_exponent: NotRequired[float]


@dataclass(frozen=True)
class EvidenceIndex:
    objects: tuple[EnvironmentObject, ...]
    tree: STRtree


def raster_directory() -> Path:
    return Path(os.environ.get("RUNMAP_ENVIRONMENT_RASTER_DIRECTORY", str(DEFAULT_DIRECTORY)))


def raster_manifest(dataset: str | None = None) -> RasterManifest:
    try:
        manifest = json.loads((raster_directory() / "manifest.json").read_text())
    except FileNotFoundError as exc:
        raise HTTPException(404, "Environment rasters have not been prepared") from exc
    if dataset is not None and dataset != manifest["dataset_id"]:
        raise HTTPException(409, "Environment raster changed; reload the inspection map")
    if read_manifest()["features"]["sha256"] != manifest["source_features"]["sha256"]:
        raise HTTPException(409, "Raster and displayed source identities differ; prepare matching rasters")
    if file_identity(source_directory() / "manifest.json")["sha256"] != manifest["source_manifest"]["sha256"]:
        raise HTTPException(409, "Acquisition manifest changed; prepare matching rasters")
    return manifest


@router.get("/config")
def raster_config(source: str = Query(min_length=64, max_length=64)) -> dict[str, object]:
    manifest = raster_manifest()
    if source != manifest["source_features"]["sha256"]:
        raise HTTPException(409, "Source changed; reload the inspection map")
    dataset = manifest["dataset_id"]
    return {"manifest": manifest, "image_template": f"/api/environment/rasters/{dataset}/{{layer}}.png",
            "sample_url": f"/api/environment/rasters/sample?dataset={dataset}"}


@router.get("/{dataset}/{layer}.png")
def raster_image(dataset: str, layer: str) -> FileResponse:
    if layer not in LAYERS:
        raise HTTPException(404, "Unknown environment raster layer")
    raster_manifest(dataset)
    path = raster_directory() / f"{layer}.png"
    if not path.is_file():
        raise HTTPException(404, "Prepared raster preview is missing")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


@lru_cache(maxsize=2)
def load_evidence(directory: Path, dataset: str) -> EvidenceIndex:
    """Bound the pilot cache to two immutable bundle identities."""
    document = json.loads((directory / "evidence.json").read_text())
    objects = tuple(EnvironmentObject(obj["id"], obj["tags"], shape(obj["geometry"]), tuple(obj["categories"]))
                    for obj in document["objects"])
    return EvidenceIndex(objects, STRtree([obj.geometry for obj in objects]))


def nearby_evidence(directory: Path, manifest: RasterManifest, point: Point) -> dict[str, object]:
    index = load_evidence(directory, manifest["dataset_id"])
    config = manifest["config"]
    road_radius = float(config["road_radius_m"])
    forest_radius = float(config["forest_radius_m"])
    water_radius = float(config["water_radius_m"])
    weights = config["road_weights"]
    noise_rules = config.get("road_noise")
    noise = RoadNoiseConfig(**noise_rules) if noise_rules is not None else None
    if noise is not None:
        road_radius = max(audible_distance(weight, noise.spreading_exponents[kind], noise)
                          for kind, weight in weights.items())
    radius = max(road_radius, forest_radius, water_radius)
    groups: dict[str, list[EvidenceItem]] = {name: [] for name in
        ("forest", "greenery", "park", "water", "traffic", "wetland", "forest_candidates", "water_candidates")}
    candidates = index.tree.query(box(point.x - radius, point.y - radius, point.x + radius, point.y + radius))
    for position in candidates:
        obj = index.objects[int(position)]
        for category in obj.categories:
            all_water = config.get("include_all_water_polygons", False)
            greenery = config["schema_version"] >= 3
            geometry = obj.geometry.boundary if category == "lake" or (category == "other_water" and all_water) else obj.geometry
            distance = geometry.distance(point)
            if greenery and category == "forest" and distance <= forest_radius:
                groups["greenery"].append({"id": obj.id, "distance_m": round(distance, 2),
                                           "category": category, "tags": obj.tags})
            if category == "park" and obj.tags.get("leisure") == "park" and distance <= 15:
                groups["park"].append({"id": obj.id, "distance_m": round(distance, 2),
                                      "category": category, "tags": obj.tags})
            designation_group = ("greenery", 15) if greenery else ("forest_candidates", forest_radius)
            group, limit = {"forest": ("forest", forest_radius), "park": designation_group,
                            "nature_reserve": designation_group,
                            "lake": ("water", water_radius), "sea": ("water", water_radius),
                            "other_water": ("water" if all_water else "water_candidates", water_radius),
                            "wetland": ("wetland", 15), "road": ("traffic", road_radius)}[category]
            kind = road_class(obj) or ""
            if category == "road" and weights.get(kind, 0) <= 0:
                continue
            if category == "road" and noise is not None:
                limit = audible_distance(weights[kind], noise.spreading_exponents[kind], noise)
            if distance > limit:
                continue
            entry: EvidenceItem = {"id": obj.id, "distance_m": round(distance, 2),
                                        "category": category, "tags": obj.tags}
            if category == "road":
                if noise is None:
                    entry["influence_at_exact_distance"] = weights.get(kind, 0) * max(0, 1 - distance / road_radius)
                else:
                    exponent = noise.spreading_exponents[kind]
                    level = intensity_to_db(source_intensity(distance, weights[kind], exponent, noise), noise)
                    entry["modeled_noise_db"] = float(level)
                    entry["spreading_exponent"] = exponent
                    entry["influence_at_exact_distance"] = float(discomfort(level, noise))
            groups[group].append(entry)
    for name, values in groups.items():
        unique: dict[str, EvidenceItem] = {}
        for item in values:
            unique.setdefault(item["id"], item)
        values[:] = unique.values()
        values.sort(key=(lambda item: -item.get("modeled_noise_db", item.get("influence_at_exact_distance", 0))) if name == "traffic"
                    else (lambda item: item["distance_m"]))
    return {name: {"count": len(values), "nearest_or_strongest": values[:5]} for name, values in groups.items()}


@router.get("/sample")
def raster_sample(dataset: str, longitude: float = Query(ge=-180, le=180),
                  latitude: float = Query(ge=-85, le=85)) -> dict[str, object]:
    manifest = raster_manifest(dataset)
    directory = raster_directory()
    xs, ys = transform("EPSG:4326", "EPSG:3067", [longitude], [latitude])
    x, y = xs[0], ys[0]
    with rasterio.open(directory / "fields.tif") as raster:
        row, column = raster.index(x, y)
        if not 0 <= row < raster.height or not 0 <= column < raster.width:
            return {"dataset_id": dataset, "status": "outside_pilot", "values": None}
        values = next(raster.sample([(x, y)]))
        result = {name: (float(value) if math.isfinite(float(value)) and value != raster.nodata else None)
                  for name, value in zip(raster.descriptions, values)}
        center = raster.xy(row, column)
    return {"dataset_id": dataset, "status": "mapped_context_only", "mapping_completeness": "unverified",
            "cell": {"row": row, "column": column, "center_epsg3067": list(center)},
            "values": result, "evidence": nearby_evidence(directory, manifest, Point(x, y))}

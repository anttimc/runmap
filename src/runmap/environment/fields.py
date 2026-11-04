"""Build reproducible metric raster proxies with separate evidence-gap channels."""

from dataclasses import dataclass
import math

from affine import Affine
import numpy as np
from numpy.typing import NDArray
from rasterio.features import rasterize
from scipy.ndimage import convolve, distance_transform_edt
from shapely.geometry.base import BaseGeometry

from runmap.environment.objects import EnvironmentObject, road_class
from runmap.environment.noise import RoadNoiseConfig, source_intensity, intensity_to_db, discomfort


FloatGrid = NDArray[np.float32]


@dataclass(frozen=True)
class BaselineConfig:
    schema_version: int
    resolution_m: int
    coverage_resolution_m: int
    forest_radius_m: int
    water_radius_m: int
    road_radius_m: int
    road_weights: dict[str, float]
    road_noise: RoadNoiseConfig | None = None
    park_greenery_weight: float = 0.0
    reserve_greenery_weight: float = 0.0
    include_all_water_polygons: bool = False


@dataclass(frozen=True)
class Grid:
    transform: Affine
    height: int
    width: int


def make_grid(bounds: list[float], resolution: int) -> Grid:
    west, south, east, north = bounds
    if any(value % resolution for value in bounds):
        raise ValueError("Grid bounds must align to resolution")
    return Grid(Affine(resolution, 0, west, 0, -resolution, north),
                int((north - south) / resolution), int((east - west) / resolution))


def burn(geometries: list[BaseGeometry], grid: Grid, touched: bool = False) -> FloatGrid:
    if not geometries:
        return np.zeros((grid.height, grid.width), dtype=np.float32)
    return rasterize([(geometry, 1) for geometry in geometries],
                     out_shape=(grid.height, grid.width), transform=grid.transform,
                     fill=0, all_touched=touched, dtype="float32")


def geometries_for(objects: list[EnvironmentObject], category: str) -> list[BaseGeometry]:
    return [obj.geometry for obj in objects if category in obj.categories]


def area_fraction(high: FloatGrid, factor: int) -> FloatGrid:
    height, width = high.shape
    return high.reshape(height // factor, factor, width // factor, factor).mean(axis=(1, 3))


def neighborhood_mean(values: FloatGrid, radius_m: float, resolution_m: int) -> FloatGrid:
    radius = math.ceil(radius_m / resolution_m)
    ys, xs = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    kernel = ((xs * resolution_m)**2 + (ys * resolution_m)**2 <= radius_m**2).astype(np.float32)
    return convolve(values, kernel / kernel.sum(), mode="constant", cval=0)


def distance_to(geometries: list[BaseGeometry], grid: Grid) -> FloatGrid:
    mapped = burn(geometries, grid, touched=True)
    if not mapped.any():
        return np.full(mapped.shape, np.inf, dtype=np.float32)
    return distance_transform_edt(mapped == 0, sampling=grid.transform.a).astype(np.float32)


def proximity(distance: FloatGrid, radius_m: float) -> FloatGrid:
    return np.clip(1 - distance / radius_m, 0, 1).astype(np.float32)


def retained_context_distance(grid: Grid) -> FloatGrid:
    inside = np.ones((grid.height, grid.width), dtype=np.uint8)
    return distance_transform_edt(np.pad(inside, 1), sampling=grid.transform.a)[1:-1, 1:-1].astype(np.float32)


def road_fields(objects: list[EnvironmentObject], grid: Grid, config: BaselineConfig) -> dict[str, FloatGrid]:
    """Keep the strongest road class; source segmentation must not multiply sound energy."""
    shape = (grid.height, grid.width)
    linear = np.zeros(shape, dtype=np.float32)
    intensity = np.zeros(shape, dtype=np.float64)
    lower_intensity = np.zeros_like(intensity)
    outside_intensity = np.zeros_like(intensity)
    # Include a cell-diagonal margin when comparing rasterized roads to the unobserved exterior.
    uncertainty_m = math.sqrt(2) * config.resolution_m
    outside_distance = np.maximum(retained_context_distance(grid) - config.resolution_m / 2 - uncertainty_m, 0)
    for kind, weight in config.road_weights.items():
        distance = distance_to([obj.geometry for obj in objects if road_class(obj) == kind], grid)
        if config.road_noise is None:
            linear = np.maximum(linear, weight * proximity(distance, config.road_radius_m))
            continue
        noise = config.road_noise
        exponent = noise.spreading_exponents[kind]
        intensity = np.maximum(intensity, source_intensity(distance, weight, exponent, noise))
        lower_intensity = np.maximum(lower_intensity, source_intensity(distance + uncertainty_m, weight, exponent, noise))
        outside_intensity = np.maximum(outside_intensity, source_intensity(outside_distance, weight, exponent, noise))
    if config.road_noise is None:
        return {"traffic": linear}
    noise = config.road_noise
    level = intensity_to_db(intensity, noise)
    outside_level = intensity_to_db(outside_intensity, noise)
    supported = (lower_intensity >= outside_intensity) | (outside_level <= noise.discomfort_floor_db)
    return {"traffic": discomfort(level, noise), "traffic_noise_db": level,
            "traffic_context_support": supported.astype(np.float32),
            "traffic_outside_bound_db": outside_level}


def build_fields(objects: list[EnvironmentObject], grid: Grid, config: BaselineConfig) -> dict[str, FloatGrid]:
    factor = config.resolution_m // config.coverage_resolution_m
    transform = Affine(grid.transform.a / factor, 0, grid.transform.c,
                       0, grid.transform.e / factor, grid.transform.f)
    high_grid = Grid(transform, grid.height * factor, grid.width * factor)
    forest = burn(geometries_for(objects, "forest"), high_grid)
    park = burn(geometries_for(objects, "park"), high_grid)
    reserve = burn(geometries_for(objects, "nature_reserve"), high_grid)
    review_designations = burn(geometries_for(objects, "park") + geometries_for(objects, "nature_reserve"), high_grid)
    coverage = area_fraction(forest, factor)
    fields = {
        "woodland_coverage": coverage,
        "forest": neighborhood_mean(coverage, config.forest_radius_m, config.resolution_m),
        "park": area_fraction(park, factor),
        "wetland": area_fraction(burn(geometries_for(objects, "wetland"), high_grid), factor),
        "forest_evidence_gap": neighborhood_mean(
            area_fraction(((review_designations > 0) & (forest == 0)).astype(np.float32), factor),
            config.forest_radius_m, config.resolution_m),
        "water_interior": area_fraction(burn(geometries_for(objects, "lake"), high_grid), factor),
    }
    if config.schema_version >= 3:
        fields["reserve_coverage"] = area_fraction(reserve, factor)
        fields["greenery"] = np.maximum.reduce([
            fields["forest"], config.park_greenery_weight * fields["park"],
            config.reserve_greenery_weight * fields["reserve_coverage"],
        ])
    lakes = [geometry.boundary for geometry in geometries_for(objects, "lake")]
    fields["lake_distance_m"] = distance_to(lakes, grid)
    fields["sea_distance_m"] = distance_to(geometries_for(objects, "sea"), grid)
    fields["water"] = proximity(np.minimum(fields["lake_distance_m"], fields["sea_distance_m"]), config.water_radius_m)
    fields["water_evidence_gap"] = proximity(distance_to(geometries_for(objects, "other_water"), grid), config.water_radius_m)
    if config.include_all_water_polygons:
        water_polygons = geometries_for(objects, "lake") + geometries_for(objects, "other_water")
        fields["water_polygon_distance_m"] = distance_to([geometry.boundary for geometry in water_polygons], grid)
        fields["water"] = proximity(np.minimum(fields["water_polygon_distance_m"], fields["sea_distance_m"]), config.water_radius_m)
        fields["water_interior"] = area_fraction(burn(water_polygons, high_grid), factor)
        fields["water_evidence_gap"] = np.zeros_like(coverage)
    fields.update(road_fields(objects, grid, config))
    if config.schema_version >= 3:
        fields["review_candidates"] = 1 - fields["traffic_context_support"]
    # Computational support describes the retained halo, never real-world mapping completeness.
    support_distance = retained_context_distance(grid)
    fields["context_support"] = (support_distance >= max(
        config.forest_radius_m, config.water_radius_m, config.road_radius_m)).astype(np.float32)
    return fields

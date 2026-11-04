"""Normalize source evidence without equating designations with land cover."""

from dataclasses import dataclass
import json
from pathlib import Path

from rasterio.warp import transform_geom
from shapely.geometry import shape, mapping
from shapely.geometry.base import BaseGeometry


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class EnvironmentObject:
    id: str
    tags: dict[str, JsonValue]
    geometry: BaseGeometry
    categories: tuple[str, ...]


def categories(tags: dict[str, JsonValue], geometry_type: str) -> tuple[str, ...]:
    polygon = geometry_type in {"Polygon", "MultiPolygon"}
    line = geometry_type in {"LineString", "MultiLineString"}
    result: list[str] = []
    if polygon:
        if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
            result.append("forest")
        if tags.get("leisure") == "park":
            result.append("park")
        if tags.get("leisure") == "nature_reserve":
            result.append("nature_reserve")
        if tags.get("natural") == "wetland":
            result.append("wetland")
        if tags.get("water") == "lake":
            result.append("lake")
        elif tags.get("natural") == "water" or "water" in tags or "waterway" in tags or tags.get("landuse") in {"reservoir", "basin"}:
            result.append("other_water")
    if line and tags.get("natural") == "coastline":
        result.append("sea")
    if line and isinstance(tags.get("highway"), str):
        result.append("road")
    return tuple(result)


def load_objects(path: Path) -> tuple[list[EnvironmentObject], int]:
    """Keep source tags and independent memberships; reject invalid geometry as evidence."""
    document = json.loads(path.read_text())
    objects: list[EnvironmentObject] = []
    invalid = 0
    identifiers: set[str] = set()
    for feature in document["features"]:
        tags = feature["properties"]
        groups = categories(tags, feature["geometry"]["type"])
        if not groups:
            continue
        geometry = shape(transform_geom("EPSG:4326", "EPSG:3067", feature["geometry"]))
        if geometry.is_empty or not geometry.is_valid:
            invalid += 1
            continue
        identifier = f'{tags["osm_type"]}/{tags["osm_id"]}'
        if identifier not in identifiers:
            objects.append(EnvironmentObject(identifier, tags, geometry, groups))
            identifiers.add(identifier)
    return objects, invalid


def evidence_document(objects: list[EnvironmentObject]) -> dict[str, object]:
    return {"crs": "EPSG:3067", "objects": [
        {"id": obj.id, "tags": obj.tags, "categories": obj.categories,
         "geometry": mapping(obj.geometry)} for obj in objects
    ]}


def road_class(obj: EnvironmentObject) -> str | None:
    if "road" not in obj.categories:
        return None
    for key in ("motor_vehicle", "vehicle", "access"):
        if key in obj.tags:
            if obj.tags[key] == "no":
                return None
            break
    value = obj.tags.get("highway")
    return value.removesuffix("_link") if isinstance(value, str) else None

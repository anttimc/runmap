"""Expose the local, published OSM pilot for source geometry inspection."""

import json
import os
from pathlib import Path
from typing import TypedDict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from rasterio.warp import transform


router = APIRouter(prefix="/api/environment")
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[3] / "data/environment/raw/leppavaara-pilot"


class SourceIdentity(TypedDict):
    sha256: str


class SourceRequest(TypedDict):
    bounds_epsg3067: list[float]


class SourceManifest(TypedDict):
    request: SourceRequest
    features: SourceIdentity
    query_bbox_wgs84_left_bottom_right_top: list[float]


def source_directory() -> Path:
    return Path(os.environ.get("RUNMAP_ENVIRONMENT_DIRECTORY", str(DEFAULT_DIRECTORY)))


def boundary_coordinates(bounds: list[float], projected: bool) -> list[list[float]]:
    """Densify projected rectangle edges so the displayed core follows its CRS."""
    west, south, east, north = bounds
    corners = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    points = [
        (start[0] + (end[0] - start[0]) * step / 20,
         start[1] + (end[1] - start[1]) * step / 20)
        for start, end in zip(corners, corners[1:]) for step in range(20)
    ] + [corners[0]]
    if not projected:
        return [[x, y] for x, y in points]
    xs, ys = transform("EPSG:3067", "EPSG:4326", [p[0] for p in points], [p[1] for p in points])
    return [[x, y] for x, y in zip(xs, ys)]


def read_manifest() -> SourceManifest:
    directory = source_directory()
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        if not (directory / "features.geojson").is_file():
            raise FileNotFoundError
    except FileNotFoundError as exc:
        raise HTTPException(404, "Environment pilot unavailable; acquire source data first") from exc
    return manifest


@router.get("/config")
def environment_config() -> dict[str, object]:
    manifest = read_manifest()
    core = manifest["request"]["bounds_epsg3067"]
    query = manifest["query_bbox_wgs84_left_bottom_right_top"]
    boundaries = [{
        "type": "Feature", "properties": {"kind": kind},
        "geometry": {"type": "Polygon", "coordinates": [boundary_coordinates(bounds, projected)]},
    } for kind, bounds, projected in [("core", core, True), ("query", query, False)]]
    return {
        "manifest": manifest,
        "features_url": f'/api/environment/features.geojson?dataset={manifest["features"]["sha256"]}',
        "boundaries": {"type": "FeatureCollection", "features": boundaries},
    }


@router.get("/features.geojson")
def environment_features(dataset: str = Query(min_length=64, max_length=64)) -> FileResponse:
    manifest = read_manifest()
    if dataset != manifest["features"]["sha256"]:
        raise HTTPException(409, "Environment dataset changed; reload the inspection map")
    return FileResponse(source_directory() / "features.geojson", media_type="application/geo+json",
                        headers={"Cache-Control": "no-cache"})

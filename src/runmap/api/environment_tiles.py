"""Deliver bounded, identity-versioned environment tiles independently of source inspection."""

from functools import lru_cache
from io import BytesIO
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
import numpy as np
from PIL import Image
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from numpy.typing import NDArray

from runmap.api.terrain_tiles import xyz_bounds
from runmap.environment.preview import COLORS


router = APIRouter(prefix="/api/environment/tiles")
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[3] / "data/environment/prepared/helsinki-capital-area-v3"
LAYERS = ("greenery", "water", "traffic", "wetland", "support")
MIN_ZOOM = 8
MAX_ZOOM = 16
TILE_SIZE = 256
RENDER_VERSION = 1


def tile_directory() -> Path:
    return Path(os.environ.get("RUNMAP_ENVIRONMENT_TILE_DIRECTORY", str(DEFAULT_DIRECTORY)))


def read_bundle() -> tuple[Path, dict[str, object]]:
    directory = tile_directory()
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
    except FileNotFoundError as exc:
        raise HTTPException(404, "Environment layers have not been prepared") from exc
    if not (directory / "fields.tif").is_file():
        raise HTTPException(404, "Environment field raster is missing")
    return directory, manifest


@router.get("/config")
def environment_tile_config() -> dict[str, object]:
    _, manifest = read_bundle()
    bounds = manifest["bounds_epsg3067"]
    return {
        "dataset_id": manifest["dataset_id"], "method": manifest["method"],
        "bounds_wgs84": list(transform_bounds("EPSG:3067", "EPSG:4326", *bounds, densify_pts=21)),
        "tile_template": f"/api/environment/tiles/v{RENDER_VERSION}/{manifest['dataset_id']}/{{layer}}/{{z}}/{{x}}/{{y}}.png",
        "tile_size": TILE_SIZE, "min_zoom": MIN_ZOOM, "max_zoom": MAX_ZOOM,
        "layers": list(LAYERS), "colors": {key: list(COLORS[key]) for key in LAYERS if key in COLORS},
        "limitations": manifest["limitations"],
    }


def read_tile_band(raster: rasterio.io.DatasetReader, band: str, bounds: tuple[float, float, float, float]) -> NDArray[np.float32]:
    if band not in raster.descriptions:
        return np.full((TILE_SIZE, TILE_SIZE), np.nan, dtype=np.float32)
    # The VRT reads only source blocks needed for this fixed-size output window.
    with WarpedVRT(raster, crs="EPSG:3857", transform=from_bounds(*bounds, TILE_SIZE, TILE_SIZE),
                   width=TILE_SIZE, height=TILE_SIZE, nodata=np.nan,
                   dtype="float32", resampling=Resampling.nearest) as warped:
        return warped.read(raster.descriptions.index(band) + 1)


@lru_cache(maxsize=128)
def render_tile(path: Path, identity: str, stamp: int, layer: str, z: int, x: int, y: int) -> bytes:
    """Keep at most 32 MiB of uncompressed-equivalent display pixels per process."""
    bounds = xyz_bounds(z, x, y)
    rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
    with rasterio.open(path) as raster:
        context = read_tile_band(raster, "context_support", bounds)
        values = read_tile_band(raster, "traffic_context_support" if layer == "support" else layer, bounds)
        if layer == "support":
            # Valid cells have a subtle coverage tint; limited support is hatched.
            finite = np.isfinite(context)
            rgba[:, :, :3] = (95, 95, 95)
            rgba[:, :, 3] = np.where(finite, 30, 0)
            rows, columns = np.indices(context.shape)
            rows += y * TILE_SIZE
            columns += x * TILE_SIZE
            limited = finite & ((context < 1) | ~np.isfinite(values) | (values < 1))
            rgba[limited, :3] = (214, 141, 12)
            rgba[:, :, 3] = np.where(limited & ((rows + columns) % 10 < 4), 190, rgba[:, :, 3])
        else:
            rgba[:, :, :3] = COLORS[layer]
            rgba[:, :, 3] = np.round(np.where(context > 0, np.clip(np.nan_to_num(values), 0, 1), 0) * 210).astype(np.uint8)
    buffer = BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG")
    return buffer.getvalue()


@router.get("/v{version}/{dataset}/{layer}/{z}/{x}/{y}.png")
def environment_tile(version: int, dataset: str, layer: str, z: int, x: int, y: int) -> Response:
    if version != RENDER_VERSION or layer not in LAYERS:
        raise HTTPException(404, "Unknown environment tile version or layer")
    if not MIN_ZOOM <= z <= MAX_ZOOM or not 0 <= x < 1 << z or not 0 <= y < 1 << z:
        raise HTTPException(422, "Invalid environment tile coordinate")
    directory, manifest = read_bundle()
    if dataset != manifest["dataset_id"]:
        raise HTTPException(409, "Environment dataset changed; reload the map")
    path = directory / "fields.tif"
    payload = render_tile(path, dataset, path.stat().st_mtime_ns, layer, z, x, y)
    return Response(payload, media_type="image/png", headers={"Cache-Control": "public, max-age=86400, immutable"})

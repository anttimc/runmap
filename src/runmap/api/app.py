"""FastAPI application for the interactive Runmap prototype."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from runmap.api.terrain import TerrainRenderer
from runmap.api.terrain_tiles import RENDER_VERSION, TerrainTileService
from runmap.elevation.store import ElevationStore
from runmap.regions import find_region


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGION_DIRECTORY = PROJECT_ROOT / "config/regions"
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data/elevation"


@asynccontextmanager
async def lifespan(application):
    source_override = os.environ.get("RUNMAP_ELEVATION_PATH")
    if source_override:
        application.state.terrain = TerrainRenderer.from_raster(Path(source_override))
    else:
        region_id = os.environ.get("RUNMAP_REGION_ID", "leppavaara")
        region_directory = Path(os.environ.get("RUNMAP_REGION_DIRECTORY", DEFAULT_REGION_DIRECTORY))
        data_root = Path(os.environ.get("RUNMAP_DATA_ROOT", DEFAULT_DATA_ROOT))
        region = find_region(region_directory, region_id)
        catalog_path = os.environ.get("RUNMAP_CATALOG_PATH")
        store = ElevationStore(data_root, catalog_path)
        application.state.terrain = TerrainTileService(region, store)
    yield


app = FastAPI(title="Runmap", version="0.1.0", lifespan=lifespan)


@app.get("/api/config")
def config(request: Request):
    return request.app.state.terrain.config()


@app.get("/api/terrain.png")
def terrain_image(
    request: Request,
    wavelength_m: float = Query(40, ge=0, le=100),
    max_size: int = Query(1600, ge=256, le=2048),
):
    if not isinstance(request.app.state.terrain, TerrainRenderer):
        raise HTTPException(
            status_code=404,
            detail="Whole-region image rendering is available only with RUNMAP_ELEVATION_PATH",
        )
    image, elapsed = request.app.state.terrain.render_png(wavelength_m, max_size)
    return Response(
        image,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Runmap-Compute-Ms": f"{1000 * elapsed:.1f}",
            "X-Runmap-Wavelength-M": f"{wavelength_m:g}",
        },
    )


@app.get(
    "/api/regions/{region_id}/terrain/v{render_version}/w{wavelength_m}/{z}/{x}/{y}.png"
)
def terrain_tile(
    request: Request,
    region_id: str,
    render_version: int,
    wavelength_m: int,
    z: int,
    x: int,
    y: int,
):
    service = request.app.state.terrain
    if not isinstance(service, TerrainTileService):
        raise HTTPException(status_code=404, detail="Named-region tile service is not active")
    if region_id != service.region.id or render_version != RENDER_VERSION:
        raise HTTPException(status_code=404, detail="Unknown region or render version")
    try:
        image, elapsed = service.render_tile(wavelength_m, z, x, y)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        image,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Runmap-Compute-Ms": f"{1000 * elapsed:.1f}",
            "X-Runmap-Wavelength-M": str(wavelength_m),
            "X-Runmap-Manifest": service.manifest_id,
        },
    )


web_dist = PROJECT_ROOT / "web/dist"
if web_dist.is_dir():
    app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
else:
    @app.get("/", response_class=HTMLResponse)
    def development_help():
        return """<!doctype html><title>Runmap API</title>
        <h1>Runmap API is running</h1>
        <p>Start the Vite development server from <code>web/</code> to open the map.</p>"""

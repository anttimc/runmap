"""FastAPI application for the interactive Runmap prototype."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from runmap.api.terrain import TerrainRenderer


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = PROJECT_ROOT / "data/raw/leppavaara/elevation-2m.tif"


@asynccontextmanager
async def lifespan(application):
    source = Path(os.environ.get("RUNMAP_ELEVATION_PATH", DEFAULT_SOURCE))
    application.state.terrain = TerrainRenderer.from_raster(source)
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


web_dist = PROJECT_ROOT / "web/dist"
if web_dist.is_dir():
    app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
else:
    @app.get("/", response_class=HTMLResponse)
    def development_help():
        return """<!doctype html><title>Runmap API</title>
        <h1>Runmap API is running</h1>
        <p>Start the Vite development server from <code>web/</code> to open the map.</p>"""

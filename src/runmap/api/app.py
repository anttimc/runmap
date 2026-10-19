"""FastAPI application for the interactive Runmap prototype."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from hashlib import sha256
from time import time

from pydantic import BaseModel, Field

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from runmap.api.paths import render_paths, inspect_path, load_path_data, path_config, viewport_paths, snap_path_start, explore_paths, inspect_outing
from runmap.api.path_tiles import PathTileService, render_version as path_render_version, tile_template
from runmap.api.terrain import TerrainRenderer
from runmap.api.runs import PlannerConfiguration, RunRequest, RunResponse, generate_run
from runmap.routing.cost_settings import preference_capabilities
from runmap.api.terrain_tiles import (
    CONTOUR_INTERVAL_M,
    CONTOUR_RENDER_VERSION,
    RENDER_VERSION,
    TerrainTileService,
)
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
    graph_path = Path(os.environ.get(
        "RUNMAP_PATH_GRAPH", PROJECT_ROOT / "data/paths/prepared/leppavaara-flat-10k/graph.json"
    ))
    application.state.paths = None
    application.state.path_status = "unavailable"
    try:
        application.state.paths = load_path_data(graph_path)
        application.state.path_status = "ready"
        application.state.path_tiles = PathTileService(
            application.state.paths,
            tile_bytes=int(os.environ.get("RUNMAP_PATH_TILE_CACHE_BYTES", 64 * 1024 ** 2)),
            snapshot_bytes=int(os.environ.get("RUNMAP_PATH_SNAPSHOT_CACHE_BYTES", 128 * 1024 ** 2)),
            snapshot_ttl=float(os.environ.get("RUNMAP_PATH_SNAPSHOT_TTL", 900)),
            tile_ttl=float(os.environ.get("RUNMAP_PATH_TILE_TTL", 900)),
            preparation_deadline=float(os.environ.get("RUNMAP_PATH_PREPARATION_DEADLINE", 10)))
    except FileNotFoundError:
        logging.getLogger(__name__).info("Path artifacts unavailable: %s", graph_path)
    except (ValueError, OSError, TypeError, KeyError):
        application.state.path_status = "failed"
        logging.getLogger(__name__).exception("Cannot load path artifacts: %s", graph_path)
    yield


app = FastAPI(title="Runmap", version="0.1.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)


@app.get("/api/config")
def config(request: Request):
    return request.app.state.terrain.config()


@app.get("/api/paths/config")
def paths_config(request: Request):
    data = request.app.state.paths
    if data is None:
        return {"status": request.app.state.path_status,
                "message": "Path analysis is unavailable. Prepare graph measurements locally."}
    return {**path_config(data), "tile_template": tile_template(data, "static/{policy}"),
            "tile_min_zoom": 10, "tile_max_zoom": 16,
            "run_planner": PlannerConfiguration(
                preference_capabilities=preference_capabilities(data.hills),
                hill_model=data.hills.model if data.hills else None,
                deadline_s=float(os.environ.get("RUNMAP_RUN_DEADLINE", "15"))).model_dump()}


def _path_data(request, dataset_id):
    data = request.app.state.paths
    if data is None:
        raise HTTPException(status_code=503, detail="Path analysis is unavailable")
    if dataset_id is not None and dataset_id != data.dataset_id:
        raise HTTPException(status_code=409, detail="Path dataset changed; reload the map")
    return data


class SnapshotRequest(BaseModel):
    longitude: float
    latitude: float
    dataset_id: str
    budget_km: float = Field(3, gt=0, le=50)
    radius_m: float = Field(50, gt=0, le=200)
    surface: str = "all"
    avoid_stairs: bool = True
    segment_id: str | None = None


@app.post("/api/paths/run")
def run_route(request: Request, options: RunRequest) -> RunResponse:
    data = _path_data(request, options.dataset_id)
    try:
        return generate_run(data, options, float(os.environ.get("RUNMAP_RUN_DEADLINE", "15")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _tile_service(request: Request, dataset_id: str) -> PathTileService:
    data = _path_data(request, dataset_id)
    service = getattr(request.app.state, "path_tiles", None)
    if service is None or service.data is not data:
        service = PathTileService(data)
        request.app.state.path_tiles = service
    return service


@app.post("/api/paths/snapshots")
def prepare_path_snapshot(request: Request, options: SnapshotRequest) -> JSONResponse:
    service = _tile_service(request, options.dataset_id)
    try:
        result = service.prepare(options.longitude, options.latitude, options.budget_km,
                                 options.surface, options.avoid_stairs, options.radius_m, options.segment_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


def _path_tile_response(request: Request, service: PathTileService, version: str,
                        policy: str, variant: str, z: int, x: int, y: int,
                        snapshot_id: str | None = None) -> Response:
    if version != path_render_version(service.data):
        raise HTTPException(404, "Unknown path rendering version")
    try:
        snapshot = service.snapshot(snapshot_id) if snapshot_id else None
        encoded, hit, elapsed = service.tile(snapshot.policy if snapshot else policy, variant, z, x, y, snapshot)
    except KeyError as exc:
        raise HTTPException(410, "Exploration snapshot expired; prepare it again") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    lifetime = max(0, int(snapshot.expires_at - time())) if snapshot else 31536000
    headers = {
        "Cache-Control": f"private, max-age={lifetime}" if snapshot else "public, max-age=31536000, immutable",
        "ETag": 'W/"' + sha256(encoded).hexdigest() + '"',
        "X-Runmap-Compute-Ms": f"{elapsed:.2f}", "X-Runmap-Cache-Hit": str(hit).lower(),
        "X-Runmap-Tile-Cache-Bytes": str(service.tiles.bytes),
        "X-Runmap-Snapshot-Cache-Bytes": str(service.snapshots.bytes),
        "Vary": "Accept-Encoding",
    }
    validators = {value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")}
    if headers["ETag"].removeprefix("W/") in validators or "*" in validators:
        return Response(status_code=304, headers=headers)
    return Response(encoded, media_type="application/vnd.mapbox-vector-tile", headers=headers)


@app.get("/api/paths/tiles/v{version}/{dataset_id}/static/{policy}/{variant}/{z}/{x}/{y}.mvt")
def static_path_tile(request: Request, version: str, dataset_id: str, policy: str,
                     variant: str, z: int, x: int, y: int) -> Response:
    return _path_tile_response(request, _tile_service(request, dataset_id), version, policy, variant, z, x, y)


@app.get("/api/paths/tiles/v{version}/{dataset_id}/snapshots/{snapshot_id}/{variant}/{z}/{x}/{y}.mvt")
def explored_path_tile(request: Request, version: str, dataset_id: str, snapshot_id: str,
                       variant: str, z: int, x: int, y: int) -> Response:
    return _path_tile_response(request, _tile_service(request, dataset_id), version, "", variant, z, x, y, snapshot_id)


@app.get("/api/paths/viewport")
def paths_viewport(
    request: Request,
    west: float, south: float, east: float, north: float,
    zoom: float = Query(..., ge=0, le=24),
    surface: str = "all", dataset_id: str = None, avoid_stairs: bool = True,
    hide_building_access: bool = True,
):
    data = _path_data(request, dataset_id)
    try:
        result = viewport_paths(data, (west, south, east, north), zoom, surface, avoid_stairs=avoid_stairs,
                                hide_building_access=hide_building_access)
        return JSONResponse(render_paths(result))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/paths/segments/{segment_id}")
def path_inspection(request: Request, segment_id: str, reverse: bool = False,
                    dataset_id: str = None, surface: str = "all", avoid_stairs: bool = True,
                    longitude: float = None, latitude: float = None, hide_building_access: bool = True,
                    budget_km: float = Query(3, gt=0, le=50),
                    radius_m: float = Query(50, gt=0, le=200), start_segment_id: str = None):
    data = _path_data(request, dataset_id)
    if segment_id not in data.segments:
        raise HTTPException(status_code=404, detail="Unknown path segment")
    try:
        result = inspect_path(data, segment_id, reverse, surface, avoid_stairs, hide_building_access)
        if longitude is not None or latitude is not None:
            if longitude is None or latitude is None:
                raise ValueError("Both start coordinates are required")
            result = inspect_outing(data, result, longitude, latitude, budget_km, surface,
                                    avoid_stairs, radius_m, start_segment_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/paths/snap")
def path_start_snap(request: Request, longitude: float, latitude: float,
                    surface: str = "all", avoid_stairs: bool = True,
                    radius_m: float = Query(50, gt=0, le=200), segment_id: str = None,
                    dataset_id: str = None):
    data = _path_data(request, dataset_id)
    try:
        return snap_path_start(data, longitude, latitude, surface, avoid_stairs, radius_m, segment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/paths/explore")
def path_exploration(request: Request, west: float, south: float, east: float, north: float,
                     zoom: float, longitude: float, latitude: float,
                     budget_km: float = Query(3, gt=0, le=50),
                     surface: str = "all", avoid_stairs: bool = True,
                     radius_m: float = Query(50, gt=0, le=200), segment_id: str = None,
                     dataset_id: str = None, metric: str = "gradient", hide_building_access: bool = True):
    data = _path_data(request, dataset_id)
    try:
        result = explore_paths(data, (west, south, east, north), zoom, longitude, latitude,
                               budget_km, surface, avoid_stairs, radius_m, segment_id, hide_building_access)
        return JSONResponse(render_paths(result, metric))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    "/api/regions/{region_id}/terrain/v{render_version}/{manifest_id}"
    "/w{wavelength_m}/{z}/{x}/{y}.png"
)
def terrain_tile(
    request: Request,
    region_id: str,
    render_version: int,
    manifest_id: str,
    wavelength_m: int,
    z: int,
    x: int,
    y: int,
):
    service = request.app.state.terrain
    if not isinstance(service, TerrainTileService):
        raise HTTPException(status_code=404, detail="Named-region tile service is not active")
    if (
        region_id != service.region.id
        or render_version != RENDER_VERSION
        or manifest_id != service.manifest_id
    ):
        raise HTTPException(status_code=404, detail="Unknown region, manifest, or render version")
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


@app.get(
    "/api/regions/{region_id}/contours/v{render_version}/{manifest_id}/{z}/{x}/{y}.mvt"
)
def contour_tile(
    request: Request,
    region_id: str,
    render_version: int,
    manifest_id: str,
    z: int,
    x: int,
    y: int,
):
    service = request.app.state.terrain
    if not isinstance(service, TerrainTileService):
        raise HTTPException(status_code=404, detail="Named-region tile service is not active")
    if (
        region_id != service.region.id
        or render_version != CONTOUR_RENDER_VERSION
        or manifest_id != service.manifest_id
    ):
        raise HTTPException(status_code=404, detail="Unknown region, manifest, or render version")
    try:
        image, elapsed = service.render_contour_tile(z, x, y)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        image,
        media_type="application/vnd.mapbox-vector-tile",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Runmap-Compute-Ms": f"{1000 * elapsed:.1f}",
            "X-Runmap-Contour-Interval-M": str(CONTOUR_INTERVAL_M),
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

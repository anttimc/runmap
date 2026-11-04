"""FastAPI application for the interactive Runmap prototype."""

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from hashlib import sha256
from time import time

from pydantic import BaseModel, Field

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from runmap.api.paths import render_paths, load_path_data, path_config, viewport_paths, snap_path_start
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import RunAreaLimits, generate_database_run, validate_run_limits
from runmap.api.sqlite_paths import (database_path_config, database_viewport, database_snap)
from runmap.api.sqlite_exploration import ExplorationAreaError, ExplorationLimits
from runmap.paths.database_pool import DatabaseBusyError
from runmap.api.path_tiles import PathTileService, render_version as path_render_version, tile_template
from runmap.api.path_shells import PathShellService
from runmap.api.terrain import TerrainRenderer
from runmap.api.runs import PlannerConfiguration, RunRequest, RunResponse
from runmap.api.route_profile import ProfileRequest, RouteProfile, build_route_profile
from runmap.paths.database import query_segment_record
from runmap.routing.cost_settings import preference_capabilities, prepared_preference_capabilities
from runmap.api.terrain_tiles import (
    CONTOUR_INTERVAL_M,
    CONTOUR_RENDER_VERSION,
    RENDER_VERSION,
    TerrainTileService,
)
from runmap.elevation.store import ElevationStore
from runmap.regions import find_region
from runmap.api.environment import router as environment_router
from runmap.api.environment_rasters import router as environment_raster_router


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
        "RUNMAP_PATH_GRAPH", PROJECT_ROOT / "data/paths/prepared/helsinki-capital-area/graph.json"
    ))
    application.state.paths = None
    application.state.path_status = "unavailable"
    database_path = os.environ.get("RUNMAP_PATH_DATABASE", str(graph_path.with_name("network.sqlite")))
    path_backend = os.environ.get("RUNMAP_PATH_BACKEND", "sqlite")
    try:
        if path_backend not in {"sqlite", "json"}:
            raise ValueError("RUNMAP_PATH_BACKEND must be sqlite or json")
        application.state.paths = (SQLitePathData(
            Path(database_path),
            pool_size=int(os.environ.get("RUNMAP_PATH_DATABASE_CONNECTIONS", "2")),
            cache_kib=int(os.environ.get("RUNMAP_PATH_DATABASE_CACHE_KIB", "16384")),
            numeric_path=Path(os.environ["RUNMAP_NUMERIC_DATABASE"]) if os.environ.get("RUNMAP_NUMERIC_DATABASE") else None,
            numeric_cache_bytes=int(os.environ.get("RUNMAP_NUMERIC_CACHE_BYTES", 128 * 1024**2)))
            if path_backend == "sqlite" else load_path_data(graph_path))
        application.state.run_limits = RunAreaLimits(
            max_area_km2=float(os.environ.get("RUNMAP_RUN_MAX_AREA_KM2", 2500)),
            max_segments=int(os.environ.get("RUNMAP_RUN_MAX_SEGMENTS", 650000)),
            max_retries=int(os.environ.get("RUNMAP_RUN_MAX_RETRIES", 5)),
            minimum_dimension_m=float(os.environ.get("RUNMAP_RUN_MIN_DIMENSION_M", 1000)),
            boundary_margin_m=float(os.environ.get("RUNMAP_RUN_BOUNDARY_MARGIN_M", 100)))
        validate_run_limits(application.state.run_limits)
        application.state.path_status = "ready"
        exploration_defaults = ExplorationLimits()
        application.state.path_tiles = PathTileService(
            application.state.paths,
            tile_bytes=int(os.environ.get("RUNMAP_PATH_TILE_CACHE_BYTES", 64 * 1024 ** 2)),
            snapshot_bytes=int(os.environ.get("RUNMAP_PATH_SNAPSHOT_CACHE_BYTES", 128 * 1024 ** 2)),
            snapshot_ttl=float(os.environ.get("RUNMAP_PATH_SNAPSHOT_TTL", 900)),
            tile_ttl=float(os.environ.get("RUNMAP_PATH_TILE_TTL", 900)),
            preparation_deadline=float(os.environ.get("RUNMAP_PATH_PREPARATION_DEADLINE", 10)),
            exploration_limits=ExplorationLimits(
                max_area_km2=float(os.environ.get("RUNMAP_PATH_SEARCH_MAX_AREA_KM2", exploration_defaults.max_area_km2)),
                max_segments=int(os.environ.get("RUNMAP_PATH_SEARCH_MAX_SEGMENTS", exploration_defaults.max_segments)),
                max_retries=int(os.environ.get("RUNMAP_PATH_SEARCH_MAX_RETRIES", exploration_defaults.max_retries))),
            tile_input_bytes=int(os.environ.get("RUNMAP_PATH_TILE_INPUT_CACHE_BYTES", 64 * 1024**2)))
    except FileNotFoundError:
        logging.getLogger(__name__).info("Path artifacts unavailable: %s", database_path or graph_path)
    except (ValueError, OSError, TypeError, KeyError, sqlite3.Error):
        if isinstance(application.state.paths, SQLitePathData):
            application.state.paths.close()
        application.state.paths = None
        application.state.path_status = "failed"
        logging.getLogger(__name__).exception("Cannot load path artifacts: %s", database_path or graph_path)
    try:
        yield
    finally:
        if isinstance(application.state.paths, SQLitePathData):
            application.state.paths.close()


app = FastAPI(title="Runmap", version="0.1.0", lifespan=lifespan)
app.include_router(environment_router)
app.include_router(environment_raster_router)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)


@app.exception_handler(DatabaseBusyError)
async def path_service_unavailable(request: Request, exc: RuntimeError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(ExplorationAreaError)
async def exploration_area_limited(request: Request, exc: ExplorationAreaError) -> JSONResponse:
    return JSONResponse({"detail": str(exc), "status": "area_limited", "reason": exc.reason,
                         "search_bounds_epsg3067": exc.bounds, "search_retries": exc.retries}, status_code=422)


@app.get("/api/config")
def config(request: Request):
    return request.app.state.terrain.config()


@app.get("/api/paths/config")
def paths_config(request: Request):
    data = request.app.state.paths
    if data is None:
        return {"status": request.app.state.path_status,
                "message": "Path analysis is unavailable. Prepare graph measurements locally."}
    sqlite = isinstance(data, SQLitePathData)
    model = data.metadata.models.hill_model if sqlite else data.hills.model if data.hills else None
    capabilities = (prepared_preference_capabilities(model, data.metadata.hill_valid_fraction)
                    if sqlite else preference_capabilities(data.hills))
    return {**(database_path_config(data) if sqlite else path_config(data)), "tile_template": tile_template(data),
            "tile_min_zoom": 13, "tile_max_zoom": 16,
            "shell_methods": ["grid", "triangulation"] if sqlite and data.numeric is not None else [],
            "run_planner": PlannerConfiguration(
                available=True,
                preference_capabilities=capabilities,
                hill_model=model,
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
        timeout = float(os.environ.get("RUNMAP_RUN_DEADLINE", "15"))
        if isinstance(data, SQLitePathData):
            return generate_database_run(data, options, timeout, getattr(request.app.state, "run_limits", RunAreaLimits()))
        from runmap.routing_reference.api import generate_run

        return generate_run(data, options, timeout)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/paths/run/profile")
def route_profile(request: Request, options: ProfileRequest) -> RouteProfile:
    data = _path_data(request, options.dataset_id)
    if sum(len(leg) for leg in options.legs) > 5000:
        raise HTTPException(400, "Route profile exceeds 5000 traversals")
    identifiers = {item.segment_id for leg in options.legs for item in leg}
    try:
        if isinstance(data, SQLitePathData):
            method = data.method
            with data.pool.connection() as connection:
                segments = {key: query_segment_record(connection, key) for key in identifiers}
        else:
            method = data.measurements.method
            segments = {key: data.segments[key] for key in identifiers}
        manifest = method.get("manifest_id")
        wavelength = method["wavelength_m"]
        terrain = request.app.state.terrain
        paths = []
        if isinstance(terrain, TerrainTileService) and manifest:
            paths = [height for path in terrain.store.ready_derived_paths(manifest, wavelength)
                     if (height := path.with_suffix(".height.tif")).is_file()]
        return build_route_profile(data.dataset_id, manifest, wavelength, options.legs, segments, paths)
    except KeyError as exc:
        raise HTTPException(404, "Unknown route segment") from exc
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
                        policy: str, variant: str, z: int, x: int, y: int) -> Response:
    if version != path_render_version(service.data):
        raise HTTPException(404, "Unknown path rendering version")
    try:
        encoded, hit, elapsed = service.tile(policy, variant, z, x, y)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "ETag": 'W/"' + sha256(encoded).hexdigest() + '"',
        "X-Runmap-Compute-Ms": f"{elapsed:.2f}", "X-Runmap-Cache-Hit": str(hit).lower(),
        "X-Runmap-Tile-Cache-Bytes": str(service.tiles.bytes),
        "X-Runmap-Tile-Input-Cache-Bytes": str(service.tile_inputs.bytes),
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


@app.get("/api/paths/snapshots/{snapshot_id}/shells")
def distance_shells(request: Request, snapshot_id: str, dataset_id: str,
                    method: Literal["grid", "triangulation"] = "grid", cell_m: int = 50,
                    subtract_straight_line: bool = False, smoothing_m: int = 25,
                    extra_travel_percentage: bool = False) -> Response:
    paths = _tile_service(request, dataset_id)
    service = getattr(request.app.state, "path_shells", None)
    if service is None or service.paths is not paths:
        service = PathShellService(paths)
        request.app.state.path_shells = service
    try:
        snapshot = paths.snapshot(snapshot_id)
        encoded, hit, elapsed = service.render(snapshot, method, cell_m, subtract_straight_line, smoothing_m, extra_travel_percentage)
    except KeyError as exc:
        raise HTTPException(410, "Exploration snapshot expired; prepare it again") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    if snapshot.expires_at <= time():
        raise HTTPException(410, "Exploration snapshot expired; prepare it again")
    headers = {"Cache-Control": f"private, max-age={max(0, int(snapshot.expires_at - time()))}",
               "X-Runmap-Compute-Ms": f"{elapsed:.2f}", "X-Runmap-Cache-Hit": str(hit).lower(),
               "X-Runmap-Shell-Cache-Bytes": str(service.responses.bytes),
               "ETag": 'W/"' + sha256(encoded).hexdigest() + '"', "Vary": "Accept-Encoding"}
    validators = {value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")}
    if headers["ETag"].removeprefix("W/") in validators or "*" in validators:
        return Response(status_code=304, headers=headers)
    return Response(encoded, media_type="application/geo+json", headers=headers)


@app.get("/api/paths/snapshots/{snapshot_id}/turnaround")
def turnaround_region(request: Request, snapshot_id: str, dataset_id: str,
                      target_m: float, width: float = 0.18) -> Response:
    from runmap.api.turnaround import render_region

    paths = _tile_service(request, dataset_id)
    service = getattr(request.app.state, "path_shells", None)
    if service is None or service.paths is not paths:
        service = PathShellService(paths)
        request.app.state.path_shells = service
    try:
        snapshot = paths.snapshot(snapshot_id)
        encoded = render_region(service, snapshot, target_m, width)
    except KeyError as exc:
        raise HTTPException(410, "Exploration snapshot expired; prepare it again") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    if snapshot.expires_at <= time():
        raise HTTPException(410, "Exploration snapshot expired; prepare it again")
    return Response(encoded, media_type="application/geo+json", headers={"Cache-Control": "private, no-cache"})


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
        reader = database_viewport if isinstance(data, SQLitePathData) else viewport_paths
        result = reader(data, (west, south, east, north), zoom, surface, avoid_stairs=avoid_stairs,
                                hide_building_access=hide_building_access)
        return JSONResponse(render_paths(result))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/paths/snap")
def path_start_snap(request: Request, longitude: float, latitude: float,
                    surface: str = "all", avoid_stairs: bool = True,
                    radius_m: float = Query(50, gt=0, le=200), segment_id: str = None,
                    dataset_id: str = None):
    data = _path_data(request, dataset_id)
    try:
        reader = database_snap if isinstance(data, SQLitePathData) else snap_path_start
        return reader(data, longitude, latitude, surface, avoid_stairs, radius_m, segment_id)
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

"""Serve bounded raw traffic-light evidence without depending on environment rasters."""

import math

from fastapi import APIRouter, HTTPException, Query, Request
from rasterio.warp import transform_bounds

from runmap.api.sqlite_data import SQLitePathData
from runmap.paths.signal_points import read_signal_points


router = APIRouter(prefix="/api/paths/signals")
MAX_FEATURES = 5000


def _data(request: Request) -> SQLitePathData:
    data = getattr(request.app.state, "paths", None)
    if not isinstance(data, SQLitePathData) or data.signal_connection is None:
        raise HTTPException(404, "Mapped traffic-light evidence is unavailable")
    if data.signal_connection.execute("PRAGMA user_version").fetchone()[0] < 2:
        raise HTTPException(404, "Re-export signals to prepare map evidence")
    return data


@router.get("/config")
def signal_map_config(request: Request) -> dict[str, object]:
    data = _data(request)
    return {"dataset_id": data.dataset_id, "source": "Original routing OSM snapshot",
            "url": "/api/paths/signals", "minimum_zoom": 13,
            "legend": {"pedestrian": "Mapped pedestrian signal", "uncertain": "Other or uncertain signal"}}


@router.get("")
def signal_map_points(request: Request, dataset: str, west: float = Query(ge=-180, le=180),
                      south: float = Query(ge=-85, le=85), east: float = Query(ge=-180, le=180),
                      north: float = Query(ge=-85, le=85)) -> dict[str, object]:
    data = _data(request)
    if dataset != data.dataset_id:
        raise HTTPException(409, "Path dataset changed; reload the map")
    if not all(math.isfinite(v) for v in (west, south, east, north)) or west >= east or south >= north:
        raise HTTPException(422, "Invalid traffic-light map bounds")
    projected = transform_bounds("EPSG:4326", "EPSG:3067", west, south, east, north, densify_pts=21)
    if (projected[2] - projected[0]) * (projected[3] - projected[1]) > 2500e6:
        return {"type": "FeatureCollection", "status": "zoom_in", "features": []}
    assert data.signal_connection is not None
    features = read_signal_points(data.signal_connection, (west, south, east, north), MAX_FEATURES + 1)
    limited = len(features) > MAX_FEATURES
    return {"type": "FeatureCollection", "status": "zoom_in" if limited else "ready",
            "features": [] if limited else features}

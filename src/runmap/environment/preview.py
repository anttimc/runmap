"""Export georeferenced Web Mercator previews without shifting the metric analysis grid."""

from pathlib import Path

import numpy as np
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import array_bounds
from rasterio.warp import calculate_default_transform, reproject, transform

from runmap.environment.fields import FloatGrid, Grid


COLORS = {"forest": (30, 138, 64), "water": (20, 120, 224),
          "greenery": (30, 138, 64),
          "park": (137, 151, 44),
          "traffic": (220, 55, 32), "wetland": (139, 68, 175),
          "evidence_gap": (214, 141, 12)}


def write_previews(destination: Path, fields: dict[str, FloatGrid], grid: Grid) -> dict[str, object]:
    bounds = array_bounds(grid.height, grid.width, grid.transform)
    target, width, height = calculate_default_transform("EPSG:3067", "EPSG:3857", grid.width, grid.height, *bounds)
    left, bottom, right, top = array_bounds(height, width, target)
    xs, ys = transform("EPSG:3857", "EPSG:4326", [left, right, right, left], [top, top, bottom, bottom])
    green_layer = "greenery" if "greenery" in fields else "forest"
    layers = {name: fields[name] for name in (green_layer, "park", "water", "traffic", "wetland")}
    layers["evidence_gap"] = np.maximum(fields["forest_evidence_gap"], fields["water_evidence_gap"])
    if "traffic_context_support" in fields:
        layers["evidence_gap"] = np.maximum(layers["evidence_gap"], 1 - fields["traffic_context_support"])
    if "review_candidates" in fields:
        layers["evidence_gap"] = fields["review_candidates"]
    for name, values in layers.items():
        warped = np.full((height, width), np.nan, dtype=np.float32)
        supported = np.where(fields["context_support"] > 0, values, np.nan)
        reproject(supported, warped, src_transform=grid.transform, src_crs="EPSG:3067",
                  dst_transform=target, dst_crs="EPSG:3857", src_nodata=np.nan, dst_nodata=np.nan,
                  resampling=Resampling.nearest)
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[:, :, :3] = COLORS[name]
        rgba[:, :, 3] = np.round(np.nan_to_num(warped, nan=0) * 210).astype(np.uint8)
        if name == "evidence_gap":
            rows, columns = np.indices(warped.shape)
            rgba[:, :, 3] *= ((rows + columns) % 10 < 4).astype(np.uint8)
        Image.fromarray(rgba).save(destination / f"{name}.png")
    return {"coordinates": [[x, y] for x, y in zip(xs, ys)],
            "shape": [height, width], "colors": {name: list(color) for name, color in COLORS.items()},
            "layers": list(layers)}

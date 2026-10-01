"""Inspect elevation rasters in blocks without loading the entire raster."""

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds


def inspect_elevation(path, expected_bounds=None):
    with rasterio.open(path) as dataset:
        errors = []
        if dataset.crs != rasterio.crs.CRS.from_epsg(3067):
            errors.append("Expected EPSG:3067")
        if dataset.count != 1:
            errors.append("Expected one elevation band")
        if not np.allclose(dataset.res, (2, 2)):
            errors.append("Expected 2 m resolution")
        transform = dataset.transform
        if transform.a <= 0 or transform.e >= 0 or transform.b != 0 or transform.d != 0:
            errors.append("Expected north-up unrotated raster")
        if expected_bounds is not None and not np.allclose(
            dataset.bounds, expected_bounds, rtol=0, atol=0.01
        ):
            errors.append("Raster bounds differ from requested bounds")
        count = 0
        minimum, maximum = float("inf"), float("-inf")
        total = 0.0
        for _, window in dataset.block_windows(1):
            values = dataset.read(1, window=window, masked=True).compressed()
            values = values[np.isfinite(values)]
            count += values.size
            if values.size:
                minimum = min(minimum, float(values.min()))
                maximum = max(maximum, float(values.max()))
                total += float(values.sum(dtype=np.float64))
        if not count:
            errors.append("No valid finite elevations")
        cells = dataset.width * dataset.height
        bounds_wgs84 = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds) if dataset.crs else None
        return {
            "path": str(path), "crs": str(dataset.crs),
            "bounds": list(dataset.bounds), "bounds_wgs84": bounds_wgs84,
            "width": dataset.width, "height": dataset.height,
            "resolution_m": list(dataset.res), "dtype": dataset.dtypes[0],
            "nodata": dataset.nodata if dataset.nodata is None or np.isfinite(dataset.nodata) else str(dataset.nodata),
            "tiled": dataset.is_tiled, "block_shapes": dataset.block_shapes,
            "overviews": dataset.overviews(1), "valid_cells": int(count),
            "invalid_cells": int(cells - count), "valid_fraction": count / cells,
            "elevation_min_m": minimum if count else None,
            "elevation_max_m": maximum if count else None,
            "elevation_mean_m": total / count if count else None,
            "errors": errors, "passed": not errors,
            "note": "Structural and numeric checks only; terrain accuracy and vertical datum are not independently verified.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    bounds = json.loads(args.config.read_text())["bounds"] if args.config else None
    report = inspect_elevation(args.path, bounds)
    rendered = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered)
    print(rendered, end="")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

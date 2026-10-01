"""Download a native-resolution NLS elevation crop using the documented WCS API.

Standard-library-only acquisition; raster validation/analysis is a separate step.
"""

import base64
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


ENDPOINT = "https://avoin-karttakuva.maanmittauslaitos.fi/ortokuvat-ja-korkeusmallit/wcs/v2"
PRODUCT = "https://www.maanmittauslaitos.fi/en/maps-and-spatial-data/datasets-and-interfaces/product-descriptions/elevation-model-2-m"


@dataclass(frozen=True)
class Bounds:
    """West, south, east, north in EPSG:3067 metres."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self):
        values = (self.west, self.south, self.east, self.north)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Bounds must be finite")
        if not (0 < self.east - self.west <= 10000 and
                0 < self.north - self.south <= 10000):
            raise ValueError("WCS elevation requests must be positive and at most 10 km per side")
        if any(v % 2 != 0 for v in values):
            raise ValueError("Align bounds to the native 2 m grid")

    def as_list(self):
        return [self.west, self.south, self.east, self.north]


def coverage_url(bounds):
    """No credentials in the URL: safe for provenance and diagnostic output."""
    return ENDPOINT + "?" + urlencode([
        ("service", "WCS"), ("version", "2.0.1"),
        ("request", "GetCoverage"), ("CoverageID", "korkeusmalli_2m"),
        ("SUBSET", f"E({bounds.west:.0f},{bounds.east:.0f})"),
        ("SUBSET", f"N({bounds.south:.0f},{bounds.north:.0f})"),
        ("format", "image/tiff"), ("geotiff:compression", "LZW"),
    ])


class NoRedirect(HTTPRedirectHandler):
    """Never forward an API credential to a redirected destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_elevation(bounds, destination, api_key):
    """Stream a crop, reject non-TIFF responses, and write provenance.

    Refuses to overwrite an existing dataset. Full geospatial validation must
    follow with Rasterio/GDAL before the file is used in an experiment.
    """
    destination = Path(destination)
    metadata = destination.with_suffix(destination.suffix + ".json")
    partial = destination.with_suffix(destination.suffix + ".part")
    if any(p.exists() for p in (destination, metadata, partial)):
        raise FileExistsError("Output or partial download exists; choose a new destination")
    if not api_key.strip():
        raise ValueError("MML_API_KEY is missing")
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = coverage_url(bounds)
    token = base64.b64encode((api_key.strip() + ":").encode()).decode()
    request = Request(url, headers={
        "Authorization": "Basic " + token,
        "User-Agent": "runmap-terrain-experiment/0.1",
    })
    digest = hashlib.sha256()
    size = 0
    try:
        with build_opener(NoRedirect()).open(request, timeout=180) as response:
            with partial.open("xb") as target:
                prefix = response.read(4)
                if prefix not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
                    raise ValueError("Service returned a non-TIFF response; download rejected")
                target.write(prefix)
                digest.update(prefix)
                size += len(prefix)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                expected = response.headers.get("Content-Length")
                if expected and size != int(expected):
                    raise ValueError("Incomplete response")
        partial.rename(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    record = {
        "provider": "National Land Survey of Finland",
        "dataset": "Elevation model 2 m", "product_url": PRODUCT,
        "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "request_url": url, "requested_crs": "EPSG:3067",
        "requested_bounds": bounds.as_list(), "requested_resolution_m": 2,
        "sha256": digest.hexdigest(), "bytes": size,
        "processing": "WCS spatial subset; native resolution requested; LZW compression",
        "validation": "TIFF signature and transfer length only; raster inspection pending",
    }
    metadata.write_text(json.dumps(record, indent=2) + "\n")
    return record

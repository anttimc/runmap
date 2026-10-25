"""Bounded OpenStreetMap acquisition through the Overpass API."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
RUNNABLE_HIGHWAY_PATTERN = (
    "cycleway|footway|living_street|path|pedestrian|residential|service|steps|tertiary|track|unclassified"
)


def highway_query(bounds_wgs84, boundary_refs=()):
    """Return an Overpass query for ways and fully tagged member nodes."""
    south, west, north, east = bounds_wgs84
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("Invalid WGS84 query bounds")
    bbox = ",".join(f"{value:.7f}" for value in (south, west, north, east))
    selectors = [
        f"way[\"highway\"~\"^({RUNNABLE_HIGHWAY_PATTERN})$\"]({bbox});"
    ]
    if boundary_refs:
        pattern = "|".join(str(reference) for reference in boundary_refs)
        selectors.append(
            f"way[\"highway\"][\"ref\"~\"^({pattern})$\"]({bbox});"
        )
    return "[out:json][timeout:180];(" + "".join(selectors) + ");out body;>;out body qt;"


def download_highways(bounds_wgs84, destination, boundary_refs=(), opener=None):
    """Download one immutable Overpass JSON response and provenance sidecar."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing OSM source: {destination}")
    query = highway_query(bounds_wgs84, boundary_refs)
    request = Request(
        OVERPASS_URL,
        data=urlencode({"data": query}).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "runmap-route-experiment/0.1",
        },
        method="POST",
    )
    with (opener or build_opener()).open(request, timeout=240) as response:
        payload = response.read()
    document = json.loads(payload)
    if not isinstance(document.get("elements"), list):
        raise ValueError("Overpass response does not contain elements")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.write_bytes(payload)
    partial.replace(destination)
    provenance = {
        "schema_version": 1,
        "provider": "OpenStreetMap contributors via Overpass API",
        "licence": "ODbL 1.0",
        "licence_url": OSM_COPYRIGHT_URL,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "bounds_wgs84": list(bounds_wgs84),
        "query": query,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }
    sidecar = destination.with_suffix(destination.suffix + ".json")
    sidecar.write_text(json.dumps(provenance, indent=2) + "\n")
    return provenance

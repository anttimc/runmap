"""Validated named-region definitions and the shared EPSG:3067 source grid."""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re


REGION_SCHEMA_VERSION = 1
ANALYSIS_CRS = "EPSG:3067"
SOURCE_RESOLUTION_M = 2
SOURCE_TILE_SIZE_M = 5000
PROCESSING_HALO_M = 512
_REGION_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Tile:
    west: int
    south: int
    size_m: int = SOURCE_TILE_SIZE_M

    @property
    def east(self):
        return self.west + self.size_m

    @property
    def north(self):
        return self.south + self.size_m

    @property
    def bounds(self):
        return self.west, self.south, self.east, self.north

    @property
    def id(self):
        return f"e{self.west:07d}_n{self.south:07d}"


@dataclass(frozen=True)
class Region:
    schema_version: int
    id: str
    title: str
    bounds: tuple
    acquisition_policy: str
    public: bool
    initial_zoom: float
    attribution_label: str
    note: str
    config_digest: str

    @property
    def width_m(self):
        return self.bounds[2] - self.bounds[0]

    @property
    def height_m(self):
        return self.bounds[3] - self.bounds[1]

    @property
    def shape(self):
        return (
            int(self.height_m / SOURCE_RESOLUTION_M),
            int(self.width_m / SOURCE_RESOLUTION_M),
        )

    def source_tiles(self, halo_m=0):
        if halo_m < 0 or halo_m % SOURCE_RESOLUTION_M:
            raise ValueError("Tile halo must be a non-negative multiple of 2 m")
        west, south, east, north = self.bounds
        west -= halo_m
        south -= halo_m
        east += halo_m
        north += halo_m
        first_west = math.floor(west / SOURCE_TILE_SIZE_M) * SOURCE_TILE_SIZE_M
        first_south = math.floor(south / SOURCE_TILE_SIZE_M) * SOURCE_TILE_SIZE_M
        return tuple(
            Tile(tile_west, tile_south)
            for tile_south in range(first_south, math.ceil(north / SOURCE_TILE_SIZE_M) * SOURCE_TILE_SIZE_M, SOURCE_TILE_SIZE_M)
            for tile_west in range(first_west, math.ceil(east / SOURCE_TILE_SIZE_M) * SOURCE_TILE_SIZE_M, SOURCE_TILE_SIZE_M)
        )

    def analysis_tiles(self):
        return self.source_tiles()


def load_region(path):
    """Load and strictly validate one versioned JSON region definition."""
    path = Path(path)
    document = json.loads(path.read_text())
    required = {
        "schema_version", "id", "title", "bounds_epsg3067",
        "acquisition_policy", "public", "initial_view", "attribution_label",
    }
    missing = required - document.keys()
    if missing:
        raise ValueError(f"Region is missing: {', '.join(sorted(missing))}")
    if document["schema_version"] != REGION_SCHEMA_VERSION:
        raise ValueError(f"Unsupported region schema {document['schema_version']}")
    region_id = document["id"]
    if not isinstance(region_id, str) or not _REGION_ID.fullmatch(region_id):
        raise ValueError("Region id must be a lowercase hyphenated slug")
    if path.stem != region_id:
        raise ValueError("Region filename must match its id")
    raw_bounds = document["bounds_epsg3067"]
    if not isinstance(raw_bounds, list) or len(raw_bounds) != 4:
        raise ValueError("bounds_epsg3067 must contain west, south, east, north")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in raw_bounds):
        raise ValueError("Region bounds must be finite numbers")
    bounds = tuple(int(value) if float(value).is_integer() else float(value) for value in raw_bounds)
    west, south, east, north = bounds
    if east <= west or north <= south:
        raise ValueError("Region bounds must have positive area")
    if any(value % SOURCE_RESOLUTION_M for value in bounds):
        raise ValueError("Region bounds must align to the native 2 m grid")
    if document["acquisition_policy"] not in {"preload", "admin-request", "public-request"}:
        raise ValueError("Unsupported acquisition policy")
    if not isinstance(document["public"], bool):
        raise ValueError("public must be a boolean")
    initial_view = document["initial_view"]
    if not isinstance(initial_view, dict) or not isinstance(initial_view.get("zoom"), (int, float)):
        raise ValueError("initial_view.zoom must be numeric")
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return Region(
        schema_version=document["schema_version"],
        id=region_id,
        title=str(document["title"]),
        bounds=bounds,
        acquisition_policy=document["acquisition_policy"],
        public=document["public"],
        initial_zoom=float(initial_view["zoom"]),
        attribution_label=str(document["attribution_label"]),
        note=str(document.get("note", "")),
        config_digest=hashlib.sha256(canonical).hexdigest(),
    )


def find_region(directory, region_id):
    """Resolve an id without allowing path traversal."""
    if not isinstance(region_id, str) or not _REGION_ID.fullmatch(region_id):
        raise ValueError("Invalid region id")
    return load_region(Path(directory) / f"{region_id}.json")


def list_regions(directory):
    return tuple(load_region(path) for path in sorted(Path(directory).glob("*.json")))

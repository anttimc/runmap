"""Persistent local elevation-tile store and immutable coverage manifests."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
import rasterio
from rasterio.shutil import copy as copy_raster
from rasterio.transform import from_origin
from rasterio.windows import Window

from runmap.elevation.inspect import inspect_elevation
from runmap.regions import (
    ANALYSIS_CRS,
    PROCESSING_HALO_M,
    SOURCE_RESOLUTION_M,
)
from runmap.sources.mml import Bounds, download_elevation
from runmap.terrain.derivatives import terrain_gradient, uphill_bearing_degrees
from runmap.terrain.smoothing import thin_plate_smooth


DERIVED_ALGORITHM_VERSION = 1
DERIVED_NODATA = 65535
MAGNITUDE_SCALE = 0.0001
BEARING_SCALE_DEGREES = 0.01


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


class ElevationStore:
    """Catalog source versions once and assemble named regions from shared tiles."""

    def __init__(self, root, catalog_path=None):
        self.root = Path(root)
        self.catalog_path = Path(catalog_path) if catalog_path else self.root / "catalog.sqlite"

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS acquisition_batches (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_tiles (
                    batch_id TEXT NOT NULL REFERENCES acquisition_batches(id),
                    tile_id TEXT NOT NULL,
                    west INTEGER NOT NULL,
                    south INTEGER NOT NULL,
                    east INTEGER NOT NULL,
                    north INTEGER NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    raw_path TEXT NOT NULL,
                    prepared_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    valid_cells INTEGER NOT NULL,
                    invalid_cells INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ready', 'failed', 'stale')),
                    PRIMARY KEY (batch_id, tile_id)
                );
                CREATE INDEX IF NOT EXISTS source_tiles_ready
                    ON source_tiles(tile_id, status, retrieved_at DESC);
                CREATE TABLE IF NOT EXISTS coverage_manifests (
                    id TEXT PRIMARY KEY,
                    region_id TEXT NOT NULL,
                    config_digest TEXT NOT NULL,
                    mapping_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coverage_manifest_tiles (
                    manifest_id TEXT NOT NULL REFERENCES coverage_manifests(id),
                    tile_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    PRIMARY KEY (manifest_id, tile_id),
                    FOREIGN KEY (batch_id, tile_id) REFERENCES source_tiles(batch_id, tile_id)
                );
                CREATE TABLE IF NOT EXISTS region_state (
                    region_id TEXT PRIMARY KEY,
                    manifest_id TEXT NOT NULL REFERENCES coverage_manifests(id),
                    config_digest TEXT NOT NULL,
                    activated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS derived_tiles (
                    manifest_id TEXT NOT NULL REFERENCES coverage_manifests(id),
                    tile_id TEXT NOT NULL,
                    wavelength_m INTEGER NOT NULL,
                    algorithm_version INTEGER NOT NULL,
                    halo_m INTEGER NOT NULL,
                    source_digest TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ready', 'failed', 'stale')),
                    PRIMARY KEY (
                        manifest_id, tile_id, wavelength_m,
                        algorithm_version, halo_m, source_digest
                    )
                );
                """
            )

    def _connect(self):
        connection = sqlite3.connect(self.catalog_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _relative(self, path):
        return str(Path(path).relative_to(self.root))

    def _absolute(self, path):
        return self.root / path

    def latest_ready_tile(self, tile_id):
        self.initialize()
        with self._connect() as connection:
            return connection.execute(
                """SELECT * FROM source_tiles
                   WHERE tile_id = ? AND status = 'ready'
                   ORDER BY retrieved_at DESC LIMIT 1""",
                (tile_id,),
            ).fetchone()

    def prepare_region(self, region, api_key, batch_id=None, downloader=download_elevation):
        """Download only missing shared tiles and activate an immutable manifest."""
        self.initialize()
        batch_id = batch_id or f"mml-dem2m-{datetime.now(timezone.utc).date().isoformat()}"
        selected = []
        for tile in region.source_tiles(PROCESSING_HALO_M):
            record = self.latest_ready_tile(tile.id)
            if record is None:
                if not api_key.strip():
                    raise ValueError(f"MML_API_KEY is required for missing tile {tile.id}")
                record = self._acquire_tile(tile, batch_id, api_key, downloader)
            selected.append(dict(record))
        return self._activate_manifest(region, selected)

    def _acquire_tile(self, tile, batch_id, api_key, downloader):
        raw_path = self.root / "raw" / batch_id / f"{tile.id}.tif"
        prepared_path = self.root / "prepared" / batch_id / f"{tile.id}.tif"
        inspection_path = raw_path.with_suffix(".inspection.json")
        bounds = Bounds(*tile.bounds)
        if not raw_path.exists():
            provenance = downloader(bounds, raw_path, api_key)
        else:
            provenance_path = raw_path.with_suffix(raw_path.suffix + ".json")
            if not provenance_path.exists():
                raise ValueError(f"Raw tile exists without provenance: {raw_path}")
            provenance = json.loads(provenance_path.read_text())
        inspection = inspect_elevation(raw_path, tile.bounds)
        inspection_path.write_text(json.dumps(inspection, indent=2, allow_nan=False) + "\n")
        if not inspection["passed"] or inspection["invalid_cells"]:
            raise ValueError(f"Elevation tile {tile.id} failed validation: {inspection['errors']}")
        if not prepared_path.exists():
            self._prepare_cog(raw_path, prepared_path)
        prepared_inspection = inspect_elevation(prepared_path, tile.bounds)
        if not prepared_inspection["passed"] or prepared_inspection["invalid_cells"]:
            raise ValueError(f"Prepared tile {tile.id} failed validation")
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO acquisition_batches VALUES (?, ?, ?, ?)",
                (batch_id, "National Land Survey of Finland", "Elevation model 2 m", _utc_now()),
            )
            connection.execute(
                """INSERT OR REPLACE INTO source_tiles
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready')""",
                (
                    batch_id, tile.id, *tile.bounds,
                    provenance["retrieved_at"], self._relative(raw_path),
                    self._relative(prepared_path), provenance["sha256"],
                    provenance["bytes"], prepared_inspection["valid_cells"],
                    prepared_inspection["invalid_cells"],
                ),
            )
            return connection.execute(
                "SELECT * FROM source_tiles WHERE batch_id = ? AND tile_id = ?",
                (batch_id, tile.id),
            ).fetchone()

    @staticmethod
    def _prepare_cog(source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.unlink(missing_ok=True)
        try:
            copy_raster(
                source,
                partial,
                driver="COG",
                compress="ZSTD",
                level=9,
                predictor="FLOATING_POINT",
                blocksize=512,
                overview_resampling="AVERAGE",
            )
            partial.replace(destination)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def _activate_manifest(self, region, records):
        mapping = [
            {"tile_id": row["tile_id"], "batch_id": row["batch_id"], "sha256": row["sha256"]}
            for row in sorted(records, key=lambda item: item["tile_id"])
        ]
        canonical = json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode()
        mapping_digest = hashlib.sha256(canonical).hexdigest()
        manifest_id = f"{region.id}-{mapping_digest[:16]}"
        created_at = _utc_now()
        manifest = {
            "schema_version": 1,
            "id": manifest_id,
            "region_id": region.id,
            "region_config_digest": region.config_digest,
            "mapping_digest": mapping_digest,
            "created_at": created_at,
            "tiles": mapping,
        }
        manifest_path = self.root / "manifests" / f"{manifest_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if not manifest_path.exists():
            partial = manifest_path.with_suffix(".json.part")
            partial.write_text(json.dumps(manifest, indent=2) + "\n")
            partial.replace(manifest_path)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO coverage_manifests VALUES (?, ?, ?, ?, ?)",
                (manifest_id, region.id, region.config_digest, mapping_digest, created_at),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO coverage_manifest_tiles VALUES (?, ?, ?, ?)",
                [(manifest_id, item["tile_id"], item["batch_id"], item["sha256"]) for item in mapping],
            )
            connection.execute(
                """INSERT INTO region_state VALUES (?, ?, ?, ?)
                   ON CONFLICT(region_id) DO UPDATE SET
                     manifest_id=excluded.manifest_id,
                     config_digest=excluded.config_digest,
                     activated_at=excluded.activated_at""",
                (region.id, manifest_id, region.config_digest, created_at),
            )
        return manifest

    def active_manifest(self, region_id):
        self.initialize()
        with self._connect() as connection:
            state = connection.execute(
                "SELECT manifest_id FROM region_state WHERE region_id = ?", (region_id,)
            ).fetchone()
            if state is None:
                raise FileNotFoundError(f"Region {region_id} has not been prepared")
            return state["manifest_id"]

    def active_manifest_for_region(self, region):
        manifest_id = self.active_manifest(region.id)
        with self._connect() as connection:
            manifest = connection.execute(
                "SELECT config_digest FROM coverage_manifests WHERE id = ?", (manifest_id,)
            ).fetchone()
        if manifest is None or manifest["config_digest"] != region.config_digest:
            raise FileNotFoundError(
                f"Region {region.id} configuration changed; prepare it again"
            )
        return manifest_id

    def read_region(self, region):
        """Assemble the exact region extent from its active prepared source tiles."""
        manifest_id = self.active_manifest_for_region(region)
        elevation, transform = self.read_bounds(manifest_id, region.bounds)
        return elevation, transform, ANALYSIS_CRS, manifest_id

    def _manifest_rows(self, manifest_id):
        with self._connect() as connection:
            return connection.execute(
                """SELECT s.* FROM coverage_manifest_tiles m
                   JOIN source_tiles s ON s.batch_id=m.batch_id AND s.tile_id=m.tile_id
                   WHERE m.manifest_id=? ORDER BY s.south, s.west""",
                (manifest_id,),
            ).fetchall()

    def read_bounds(self, manifest_id, bounds):
        """Read an arbitrary native-grid rectangle from one coverage manifest."""
        region_west, region_south, region_east, region_north = bounds
        if any(value % SOURCE_RESOLUTION_M for value in bounds):
            raise ValueError("Read bounds must align to the native 2 m grid")
        height = int((region_north - region_south) / SOURCE_RESOLUTION_M)
        width = int((region_east - region_west) / SOURCE_RESOLUTION_M)
        elevation = np.full((height, width), np.nan, dtype=np.float32)
        covered = np.zeros((height, width), dtype=bool)
        for row in self._manifest_rows(manifest_id):
            left = max(region_west, row["west"])
            bottom = max(region_south, row["south"])
            right = min(region_east, row["east"])
            top = min(region_north, row["north"])
            if left >= right or bottom >= top:
                continue
            cols = int((right - left) / SOURCE_RESOLUTION_M)
            rows_count = int((top - bottom) / SOURCE_RESOLUTION_M)
            source_window = Window(
                int((left - row["west"]) / SOURCE_RESOLUTION_M),
                int((row["north"] - top) / SOURCE_RESOLUTION_M),
                cols,
                rows_count,
            )
            destination_row = int((region_north - top) / SOURCE_RESOLUTION_M)
            destination_col = int((left - region_west) / SOURCE_RESOLUTION_M)
            destination = np.s_[
                destination_row:destination_row + rows_count,
                destination_col:destination_col + cols,
            ]
            with rasterio.open(self._absolute(row["prepared_path"])) as dataset:
                values = dataset.read(1, window=source_window, masked=True).filled(np.nan)
            elevation[destination] = values
            covered[destination] = np.isfinite(values)
        if not covered.all():
            raise ValueError(f"Prepared coverage is incomplete for bounds {bounds}")
        transform = from_origin(region_west, region_north, SOURCE_RESOLUTION_M, SOURCE_RESOLUTION_M)
        return elevation, transform

    def precompute_region(self, region, wavelength_m):
        manifest_id = self.active_manifest(region.id)
        return tuple(
            self.ensure_derived_tile(manifest_id, tile, wavelength_m)
            for tile in region.analysis_tiles()
        )

    def ensure_derived_tile(self, manifest_id, tile, wavelength_m):
        wavelength = int(wavelength_m)
        if wavelength != wavelength_m or wavelength < 0 or wavelength > 100:
            raise ValueError("Wavelength must be a whole number from 0 to 100 m")
        expanded = (
            tile.west - PROCESSING_HALO_M,
            tile.south - PROCESSING_HALO_M,
            tile.east + PROCESSING_HALO_M,
            tile.north + PROCESSING_HALO_M,
        )
        contributing = [
            row for row in self._manifest_rows(manifest_id)
            if row["east"] > expanded[0] and row["west"] < expanded[2]
            and row["north"] > expanded[1] and row["south"] < expanded[3]
        ]
        canonical = json.dumps(
            [(row["tile_id"], row["sha256"]) for row in contributing],
            separators=(",", ":"),
        ).encode()
        source_digest = hashlib.sha256(canonical).hexdigest()
        with self._connect() as connection:
            ready = connection.execute(
                """SELECT * FROM derived_tiles WHERE manifest_id=? AND tile_id=?
                   AND wavelength_m=? AND algorithm_version=? AND halo_m=?
                   AND source_digest=? AND status='ready'""",
                (
                    manifest_id, tile.id, wavelength, DERIVED_ALGORITHM_VERSION,
                    PROCESSING_HALO_M, source_digest,
                ),
            ).fetchone()
        if ready is not None and self._absolute(ready["path"]).exists():
            return self._absolute(ready["path"])

        elevation, _ = self.read_bounds(manifest_id, expanded)
        smoothed = thin_plate_smooth(elevation, wavelength, SOURCE_RESOLUTION_M)
        east, north, magnitude = terrain_gradient(
            smoothed, SOURCE_RESOLUTION_M, SOURCE_RESOLUTION_M
        )
        bearing = uphill_bearing_degrees(east, north)
        halo_px = PROCESSING_HALO_M // SOURCE_RESOLUTION_M
        core = np.s_[halo_px:-halo_px, halo_px:-halo_px]
        magnitude = magnitude[core]
        bearing = bearing[core]
        encoded_magnitude = np.where(
            np.isfinite(magnitude),
            np.rint(np.clip(magnitude / MAGNITUDE_SCALE, 0, DERIVED_NODATA - 1)),
            DERIVED_NODATA,
        ).astype(np.uint16)
        encoded_bearing = np.where(
            np.isfinite(bearing),
            np.rint(bearing / BEARING_SCALE_DEGREES) % 36000,
            DERIVED_NODATA,
        ).astype(np.uint16)
        relative = Path("derived") / f"v{DERIVED_ALGORITHM_VERSION}" / source_digest[:16] / f"w{wavelength:03d}" / f"{tile.id}.tif"
        destination = self.root / relative
        self._write_derived_cog(
            destination,
            tile,
            encoded_magnitude,
            encoded_bearing,
            wavelength,
            source_digest,
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO derived_tiles
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready')""",
                (
                    manifest_id, tile.id, wavelength, DERIVED_ALGORITHM_VERSION,
                    PROCESSING_HALO_M, source_digest, str(relative), _utc_now(),
                ),
            )
        return destination

    @staticmethod
    def _write_derived_cog(destination, tile, magnitude, bearing, wavelength, source_digest):
        destination.parent.mkdir(parents=True, exist_ok=True)
        work = destination.with_suffix(".work.tif")
        partial = destination.with_suffix(".tif.part")
        work.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
        try:
            with rasterio.open(
                work,
                "w",
                driver="GTiff",
                width=magnitude.shape[1],
                height=magnitude.shape[0],
                count=2,
                dtype="uint16",
                crs=ANALYSIS_CRS,
                transform=from_origin(tile.west, tile.north, SOURCE_RESOLUTION_M, SOURCE_RESOLUTION_M),
                nodata=DERIVED_NODATA,
                tiled=True,
                blockxsize=512,
                blockysize=512,
            ) as dataset:
                dataset.write(magnitude, 1)
                dataset.write(bearing, 2)
                dataset.set_band_description(1, "slope_magnitude_rise_run")
                dataset.set_band_description(2, "uphill_bearing_grid_north_degrees")
                dataset.scales = (MAGNITUDE_SCALE, BEARING_SCALE_DEGREES)
                dataset.update_tags(
                    algorithm="spectral-thin-plate",
                    algorithm_version=DERIVED_ALGORITHM_VERSION,
                    wavelength_m=wavelength,
                    processing_halo_m=PROCESSING_HALO_M,
                    source_digest=source_digest,
                )
            copy_raster(
                work,
                partial,
                driver="COG",
                compress="ZSTD",
                level=9,
                blocksize=512,
                overview_resampling="AVERAGE",
            )
            partial.replace(destination)
        finally:
            work.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)

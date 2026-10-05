"""Acquisition entry point: PYTHONPATH=src python3 -m runmap.cli."""

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError

from runmap.elevation.store import ElevationStore
from runmap.regions import PROCESSING_HALO_M, find_region, list_regions
from runmap.sources.mml import Bounds, coverage_url, download_elevation


def local_key(path):
    """Read only MML_API_KEY from a plain local .env; never execute shell code."""
    if os.environ.get("MML_API_KEY"):
        return os.environ["MML_API_KEY"]
    if path.exists():
        for line in path.read_text().splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == "MML_API_KEY":
                return value.strip().strip("\"'")
    return ""


def legacy_download(argv=None):
    parser = argparse.ArgumentParser(description="Download a native 2 m MML elevation crop")
    parser.add_argument("--config", type=Path, default=Path("config/regions/leppavaara.json"))
    parser.add_argument("--output", type=Path, default=Path("data/raw/leppavaara/elevation-2m.tif"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--dry-run", action="store_true", help="Show the request without credentials or network access")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    if config.get("crs", "EPSG:3067") != "EPSG:3067":
        parser.error("This downloader expects EPSG:3067 bounds")
    bounds = Bounds(*config.get("bounds", config.get("bounds_epsg3067", [])))
    if args.dry_run:
        print(coverage_url(bounds))
        return
    key = local_key(args.env_file)
    if not key.strip():
        parser.error("Set MML_API_KEY in the environment or local .env; do not put it in command arguments")
    try:
        record = download_elevation(bounds, args.output, key)
    except HTTPError as exc:
        parser.exit(1, f"Download failed: HTTP {exc.code}. Check API key/service availability.\n")
    except URLError:
        parser.exit(1, "Download failed: network connection unavailable.\n")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Download failed: {exc}\n")
    print(f"Saved {args.output} ({record['bytes']} bytes); raster inspection is still required")


def region_command(argv):
    parser = argparse.ArgumentParser(description="Manage named Runmap elevation regions")
    parser.add_argument("--regions", type=Path, default=Path("config/regions"))
    parser.add_argument("--data-root", type=Path, default=Path("data/elevation"))
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List validated region definitions")
    prepare = subparsers.add_parser("prepare", help="Download and validate missing region tiles")
    prepare.add_argument("region_id")
    prepare.add_argument("--batch-id")
    prepare.add_argument("--dry-run", action="store_true")
    precompute = subparsers.add_parser("precompute", help="Build derived analysis tiles")
    precompute.add_argument("region_id")
    precompute.add_argument("--wavelength-m", type=int, default=40)
    args = parser.parse_args(argv)
    if args.command == "list":
        for region in list_regions(args.regions):
            print(f"{region.id}\t{region.title}\t{region.width_m:g} x {region.height_m:g} m")
        return
    region = find_region(args.regions, args.region_id)
    store = ElevationStore(args.data_root, args.catalog)
    if args.command == "precompute":
        paths = store.precompute_region(region, args.wavelength_m)
        print(
            f"Precomputed {len(paths)} analysis tiles for {region.id} "
            f"at {args.wavelength_m} m"
        )
        return
    if args.dry_run:
        print(f"Region {region.id}: {region.bounds} ({region.width_m:g} x {region.height_m:g} m)")
        for tile in region.source_tiles(PROCESSING_HALO_M):
            print(f"{tile.id}\t{tile.bounds}\t{coverage_url(Bounds(*tile.bounds))}")
        return
    key = local_key(args.env_file)
    try:
        manifest = store.prepare_region(region, key, batch_id=args.batch_id)
    except HTTPError as exc:
        parser.exit(1, f"Download failed: HTTP {exc.code}. Check API key/service availability.\n")
    except URLError:
        parser.exit(1, "Download failed: network connection unavailable.\n")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Region preparation failed: {exc}\n")
    print(
        f"Prepared {region.id}: {len(manifest['tiles'])} source tiles; "
        f"active manifest {manifest['id']}"
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "region":
        region_command(argv[1:])
    else:
        legacy_download(argv)


if __name__ == "__main__":
    main()

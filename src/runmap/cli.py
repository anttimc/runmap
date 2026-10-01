"""Acquisition entry point: PYTHONPATH=src python3 -m runmap.cli."""

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError

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


def main():
    parser = argparse.ArgumentParser(description="Download a native 2 m MML elevation crop")
    parser.add_argument("--config", type=Path, default=Path("config/leppavaara.json"))
    parser.add_argument("--output", type=Path, default=Path("data/raw/leppavaara/elevation-2m.tif"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--dry-run", action="store_true", help="Show the request without credentials or network access")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config["crs"] != "EPSG:3067":
        parser.error("This downloader expects EPSG:3067 bounds")
    bounds = Bounds(*config["bounds"])
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


if __name__ == "__main__":
    main()

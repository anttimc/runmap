"""Download and freeze a complete Geofabrik OSM snapshot for repeatable local imports."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

from runmap.sources.environment import file_identity, _write_json


FINLAND_URL = "https://download.geofabrik.de/europe/finland-latest.osm.pbf"


def freeze_download(url: str, directory: Path) -> dict[str, str]:
    """Pin redirects before starting so interrupted downloads cannot mix daily snapshots."""
    request_path = directory / "download.json"
    if request_path.exists():
        record = json.loads(request_path.read_text())
        if record["requested_url"] != url:
            raise ValueError("Download directory belongs to a different source URL")
        return record
    with urlopen(Request(url, method="HEAD"), timeout=60) as response:
        resolved = response.geturl()
    if "-latest." in resolved:
        raise ValueError("Expected Geofabrik to resolve latest to a dated snapshot")
    with urlopen(resolved + ".md5", timeout=60) as response:
        checksum = response.read().decode().split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{32}", checksum):
        raise ValueError("Invalid provider MD5 checksum")
    record = {"requested_url": url, "resolved_url": resolved, "provider_md5": checksum}
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(request_path, record)
    return record


def download_payload(url: str, path: Path) -> None:
    offset = path.stat().st_size if path.exists() else 0
    headers = {"User-Agent": "runmap-offline-preparation/0.1"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    with urlopen(Request(url, headers=headers), timeout=120) as response:
        resumed = response.status == 206 and offset > 0
        if resumed and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
            raise ValueError("Provider returned an unexpected byte range")
        total = offset if resumed else 0
        reported = total // (64 * 1024**2)
        with path.open("ab" if resumed else "wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
                total += len(chunk)
                if total // (64 * 1024**2) > reported:
                    reported = total // (64 * 1024**2)
                    print(f"Downloaded {total / 1024**2:.0f} MiB", flush=True)


def verify_md5(path: Path, expected: str) -> None:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError("Downloaded PBF differs from the provider checksum; partial file retained")


def download_finland(directory: Path, url: str = FINLAND_URL) -> Path:
    if (directory / "manifest.json").exists():
        raise FileExistsError(f"Completed snapshot already exists: {directory}")
    if directory.exists() and any(directory.iterdir()) and not (directory / "download.json").exists():
        raise ValueError("Refusing a nonempty directory without a download record")
    record = freeze_download(url, directory)
    partial = directory / "finland.osm.pbf.part"
    target = directory / "finland.osm.pbf"
    if target.exists():
        verify_md5(target, record["provider_md5"])
    else:
        complete_partial = False
        if partial.exists():
            try:
                verify_md5(partial, record["provider_md5"])
                complete_partial = True
            except ValueError:
                pass
        if not complete_partial:
            download_payload(record["resolved_url"], partial)
        verify_md5(partial, record["provider_md5"])
        partial.replace(target)
    manifest = {"schema_version": 1, "provider": "Geofabrik / OpenStreetMap contributors",
                **record, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "licence": "ODbL 1.0", "licence_url": "https://www.openstreetmap.org/copyright",
                "pbf": file_identity(target), "scope": "Complete provider Finland extract, unfiltered"}
    _write_json(directory / "manifest.json", manifest)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(download_finland(args.output))


if __name__ == "__main__":
    main()

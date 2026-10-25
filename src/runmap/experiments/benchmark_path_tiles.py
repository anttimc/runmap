"""Measure identical path views as complete XYZ tile sets, including gzip sizes."""
import argparse
import gzip
import json
import math
from pathlib import Path
from statistics import median
from time import perf_counter

import mapbox_vector_tile

from runmap.api.paths import load_path_data
from runmap.api.path_tiles import PathTileService, projected_point


def coordinates(bounds: tuple[float, float, float, float], z: int) -> list[tuple[int, int]]:
    west, south, east, north = bounds
    left, top = projected_point(west, north, z, 0, 0)
    right, bottom = projected_point(east, south, z, 0, 0)
    return [(x, y) for x in range(math.floor(left / 4096), math.floor(right / 4096) + 1)
            for y in range(math.floor(top / 4096), math.floor(bottom / 4096) + 1)]


def benchmark(graph: Path, repeats: int) -> dict:
    data = load_path_data(graph)
    service = PathTileService(data)
    rows = []
    for name, lon, lat in (("urban", 24.807, 60.219), ("forest", 24.771, 60.242)):
        for z, dx, dy in ((10, .14, .08), (11, .07, .04), (13, .025, .013), (15, .008, .004)):
            bounds = (lon - dx, lat - dy, lon + dx, lat + dy)
            tiles = coordinates(bounds, z)
            samples: dict[bool, list[float]] = {False: [], True: []}
            payloads: dict[bool, list[bytes]] = {}
            for repeat in range(repeats + 1):
                for hide in ((False, True) if repeat % 2 == 0 else (True, False)):
                    variant = f"gradient-{'hidden' if hide else 'all'}"
                    started = perf_counter()
                    encoded = [service.tile("all-no-stairs", variant, z, x, y)[0] for x, y in tiles]
                    samples[hide].append((perf_counter() - started) * 1000)
                    payloads[hide] = encoded
            runs = []
            for hide in (False, True):
                encoded = payloads[hide]
                features = [feature for tile in encoded for feature in mapbox_vector_tile.decode(tile)["paths"]["features"]]
                vertices = 0
                for feature in features:
                    geometry = feature["geometry"]
                    vertices += (len(geometry["coordinates"]) if geometry["type"] == "LineString" else
                                 sum(len(line) for line in geometry["coordinates"]))
                runs.append({"hide_building_access": hide, "bytes": sum(map(len, encoded)),
                             "gzip_bytes": sum(len(gzip.compress(tile, compresslevel=5, mtime=0)) for tile in encoded),
                             "features": len(features), "vertices": vertices,
                             "largest_tile_bytes": max(map(len, encoded)), "cold_ms": samples[hide][0],
                             "warm_ms": samples[hide][1:], "warm_median_ms": median(samples[hide][1:])})
            rows.append({"viewport": name, "zoom": z, "bounds": bounds, "mode": "gradient",
                         "tile_count": len(tiles), "runs": runs})
            print(name, z, "gradient", runs[-1]["bytes"], flush=True)
    return {"dataset_id": data.dataset_id, "repeats": repeats,
            "tile_cache_bytes": service.tiles.bytes,
            "method": "Complete intersecting tiles including buffer, in-process generation/cache lookup; gzip level 5, no HTTP/browser timings. Alternating visibility order. First view of a tile is cold; overlapping views may reuse tiles.",
            "viewports": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    if args.repeats < 10:
        parser.error("Use at least ten warm repetitions")
    args.output.write_text(json.dumps(benchmark(args.graph, args.repeats), indent=2) + "\n")


if __name__ == "__main__":
    main()

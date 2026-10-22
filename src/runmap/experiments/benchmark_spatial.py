"""Compare packed spatial rows against the preceding shared-integer list layout."""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from statistics import median
from time import perf_counter

from runmap.api.paths import candidate_path_indexes, load_path_data, viewport_paths
from runmap.api.path_tiles import encode_tile, projected_point


def retained(value: object, seen: set[int]) -> int:
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        return size + sum(retained(k, seen) + retained(v, seen) for k, v in value.items())
    if isinstance(value, (tuple, list)):
        return size + sum(retained(item, seen) for item in value)
    if isinstance(value, memoryview):
        return size + retained(value.obj, seen)
    return size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('graph', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = load_path_data(args.graph)
    ids = tuple(range(len(data.features)))
    cells = {cell: [ids[i] for i in indexes] for cell, indexes in data.cells.items()}
    display = {cell: [ids[i] for i in indexes] for cell, indexes in data.display_cells.items()}
    reference = replace(data, cells=cells, display_cells=display)
    rows = []
    tiles = 0
    for lon, lat in ((24.807, 60.219), (24.771, 60.242)):
        for z, dx, dy in ((13, .025, .013), (15, .008, .004)):
            bounds = (lon-dx, lat-dy, lon+dx, lat+dy)
            for hide in (False, True):
                expected = candidate_path_indexes(reference, bounds, hide)
                assert candidate_path_indexes(data, bounds, hide) == expected
                assert viewport_paths(reference, bounds, z, hide_building_access=hide) == viewport_paths(data, bounds, z, hide_building_access=hide)
                samples: list[list[float]] = [[], []]
                for repeat in range(20):
                    for which in ((0, 1) if repeat % 2 == 0 else (1, 0)):
                        started = perf_counter()
                        candidate_path_indexes((reference, data)[which], bounds, hide)
                        samples[which].append((perf_counter()-started)*1000)
                rows.append(dict(zoom=z, bounds=bounds, hidden=hide, candidates=len(expected), reference_ms=median(samples[0]), packed_ms=median(samples[1])))
                px, py = projected_point(lon, lat, z, 0, 0)
                for policy in ('all-no-stairs', 'surfaced-no-stairs'):
                    variant = 'gradient-hidden' if hide else 'gradient-all'
                    tile_args = (policy, variant, z, int(px//4096), int(py//4096))
                    assert encode_tile(reference, *tile_args) == encode_tile(data, *tile_args)
                    tiles += 1
    report = dict(dataset_id=data.dataset_id, intervals=len(ids), cells=len(cells),
                  reference_bytes=retained((cells, display), set()),
                  packed_bytes=retained((data.cells, data.display_cells), set()),
                  shared_rows=sum(data.cells[cell] is row for cell, row in data.display_cells.items()),
                  identical_tiles=tiles, queries=rows,
                  method='Reconstructed previous list layout with one shared Python integer per feature; recursive sys.getsizeof with shared visited set, including immutable buffer owners. Excludes other dataset objects. Twenty alternating candidate-query timings; exact viewport and encoded-tile comparisons. Not process RSS or browser latency.')
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

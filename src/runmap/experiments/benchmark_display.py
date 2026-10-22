"""Compare packed display metadata with independently rebuilt interval objects."""

import argparse
from dataclasses import fields, is_dataclass, replace
import json
from pathlib import Path
import platform
from statistics import median
import sys
from time import perf_counter

from runmap.api.paths import PathData, load_path_data, viewport_paths
from runmap.api.path_tiles import encode_tile, projected_point
from runmap.paths.display import DisplayInterval
from runmap.paths.flat import GradeInterval, flat_run_thresholds
from runmap.paths.measurements import segment_measurements
from runmap.routing.profiles import surface_category


def reference_intervals(data: PathData) -> tuple[DisplayInterval, ...]:
    result: list[DisplayInterval] = []
    for segment, segment_id in enumerate(data.measurements.segment_ids):
        intervals = segment_measurements(data.measurements, segment_id)
        grades = [GradeInterval(item['start_m'], item['end_m'], item['grade_percent']
                  if item['valid'] and not {'bridge', 'tunnel', 'stairs'}.intersection(item['quality_flags'])
                  else None) for item in intervals]
        surface = surface_category(data.segments[segment_id])
        result.extend(DisplayInterval(segment, item['interval_index'], surface, threshold)
                      for item, threshold in zip(intervals, flat_run_thresholds(grades)))
    return tuple(result)


def retained(value: object, seen: set[int]) -> int:
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, tuple):
        return size + sum(retained(item, seen) for item in value)
    if isinstance(value, memoryview):
        return size + retained(value.obj, seen)
    if is_dataclass(value) and not isinstance(value, type):
        size += sum(retained(getattr(value, field.name), seen) for field in fields(value))
        if hasattr(value, '__dict__'):
            size += sys.getsizeof(value.__dict__)
    return size


def compare_views(data: PathData, reference: PathData) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for lon, lat in ((24.807, 60.219), (24.771, 60.242)):
        for z, dx, dy in ((13, .025, .013), (15, .008, .004)):
            bounds = (lon-dx, lat-dy, lon+dx, lat+dy)
            for hide in (False, True):
                assert viewport_paths(data, bounds, z, hide_building_access=hide) == viewport_paths(
                    reference, bounds, z, hide_building_access=hide)
                px, py = projected_point(lon, lat, z, 0, 0)
                for policy in ('all-no-stairs', 'surfaced-no-stairs'):
                    tile_args = (policy, 'gradient-hidden' if hide else 'gradient-all',
                                 z, int(px//4096), int(py//4096))
                    assert encode_tile(reference, *tile_args) == encode_tile(data, *tile_args)
                    samples: list[list[float]] = [[], []]
                    for repeat in range(10):
                        for which in ((0, 1) if repeat % 2 == 0 else (1, 0)):
                            started = perf_counter()
                            encode_tile((reference, data)[which], *tile_args)
                            samples[which].append((perf_counter()-started)*1000)
                    rows.append(dict(longitude=lon, latitude=lat, zoom=z, hidden=hide, policy=policy,
                                     reference_ms=median(samples[0]), packed_ms=median(samples[1])))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('graph', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = load_path_data(args.graph)
    previous = reference_intervals(data)
    assert tuple(data.features) == previous
    shared = {id(data.measurements.interval_offsets)}
    report = dict(dataset_id=data.dataset_id, intervals=len(previous),
                  python=platform.python_version(), platform=platform.platform(),
                  reference_bytes=retained(previous, shared.copy()),
                  packed_bytes=retained(data.features, shared.copy()),
                  method='Independent previous-layout reconstruction from measurements. Recursive sys.getsizeof includes buffer owners and excludes shared measurement offsets. Ten alternating uncached static tile encodes per case after equality checks; eight full viewport comparisons and sixteen byte-identical tile comparisons. Not process RSS, startup, routing, or browser latency.',
                  tiles=compare_views(data, replace(data, features=previous)))
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

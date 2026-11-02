"""Measure planner width response at fixed radii and bearings from multiple starts."""

import argparse
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import platform
from time import perf_counter

from rasterio.warp import transform
from runmap import _routing_native

from runmap.api.runs import RunResponse
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_paths import database_snap
from runmap.api.sqlite_runs import RunAreaLimits, validate_run_limits
from runmap.experiments.loop_length_grid import GridConfig, GridPoint, GridSample, evaluate_point, source_identity
from runmap.routing.run_types import RunSettings
from runmap.routing.settings import validate_settings


@dataclass(frozen=True)
class SweepStart:
    name: str
    longitude: float
    latitude: float


DEFAULT_STARTS = (
    SweepStart("Leppävaara", 24.795, 60.216),
    SweepStart("Pasila", 24.934125, 60.198361),
    SweepStart("Keskuspuisto", 24.908, 60.233),
    SweepStart("Lauttasaari", 24.875, 60.160),
    SweepStart("Viikki", 25.016, 60.227),
)


@dataclass(frozen=True)
class SweepConfig:
    starts: tuple[SweepStart, ...] = DEFAULT_STARTS
    radii_m: tuple[float, ...] = (1500, 3000)
    widths: tuple[float, ...] = (0, .06, .12, .18, .24, .30, .36, .42, .50, .60)
    bearings: int = 12
    timeout_s: float = 3
    limits: RunAreaLimits = RunAreaLimits()
    workers: int = 1


@dataclass(frozen=True)
class SweepPoint:
    sample_id: int
    start: str
    radius_m: float
    bearing_deg: float
    width: float
    longitude: float
    latitude: float


@dataclass(frozen=True)
class SweepSample:
    point: SweepPoint
    response: RunResponse | None
    wall_s: float
    error: str | None


def validate_sweep(config: SweepConfig) -> None:
    validate_run_limits(config.limits)
    if config.workers not in (1, 2):
        raise ValueError("Use one or two workers to respect planner admission slots")
    if config.bearings < 4 or not math.isfinite(config.timeout_s) or config.timeout_s <= 0:
        raise ValueError("At least four bearings and a positive finite deadline are required")
    if not config.radii_m or any(not math.isfinite(r) or r <= 0 for r in config.radii_m):
        raise ValueError("Radii must be finite and positive")
    if len(set(config.radii_m)) != len(config.radii_m):
        raise ValueError("Radii must be unique")
    if not config.widths or config.widths[0] != 0 or any(a >= b for a, b in zip(config.widths, config.widths[1:])):
        raise ValueError("Widths must increase strictly from zero for matched baseline comparisons")
    for width in config.widths:
        validate_settings(RunSettings(avoidance_ratio=width))
    if not config.starts or len({start.name for start in config.starts}) != len(config.starts):
        raise ValueError("Start names must be nonempty and unique")
    for start in config.starts:
        if not start.name or not (-180 <= start.longitude <= 180 and -85 <= start.latitude <= 85):
            raise ValueError("Invalid start")


def sweep_points(start: SweepStart, config: SweepConfig) -> Iterator[SweepPoint]:
    x, y = transform("EPSG:4326", "EPSG:3067", [start.longitude], [start.latitude])
    sample_id = 0
    for radius in config.radii_m:
        for bearing in range(config.bearings):
            angle = 2 * math.pi * bearing / config.bearings
            lon, lat = transform("EPSG:3067", "EPSG:4326", [x[0] + radius * math.sin(angle)],
                                 [y[0] + radius * math.cos(angle)])
            for width in config.widths:
                yield SweepPoint(sample_id, start.name, radius, bearing * 360 / config.bearings,
                                 width, lon[0], lat[0])
                sample_id += 1


def read_samples(path: Path, *, retain_geometry: bool = True) -> Iterator[SweepSample]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            response = None if row["response"] is None else RunResponse.model_validate(row["response"])
            if response is not None and not retain_geometry:
                for candidate in response.candidates:
                    for leg in candidate.legs:
                        leg.coordinates.clear()
                        leg.traversals.clear()
            yield SweepSample(SweepPoint(**row["point"]), response, row["wall_s"], row["error"])


def evaluate_sweep_point(point: SweepPoint, *, data: SQLitePathData,
                         grid: GridConfig, limits: RunAreaLimits) -> GridSample:
    request = GridPoint(point.sample_id, 0, 0, 0, point.radius_m, 0,
                        point.width, point.longitude, point.latitude)
    return evaluate_point(data, grid, request, limits)


def sample_sweep(database: Path, output: Path, config: SweepConfig) -> None:
    validate_sweep(config)
    output.mkdir(parents=True, exist_ok=False)
    manifest_path = output / "manifest.json"
    raw_path = output / "samples.jsonl.gz"
    started = perf_counter()
    manifest: dict[str, object] = {
        "complete": False, "created_utc": datetime.now(timezone.utc).isoformat(),
        "config": asdict(config), "source": source_identity(), "machine": platform.platform(),
        "database": str(database.resolve()), "run_area_limits": asdict(config.limits),
        "policy": {"surface": "all", "avoid_stairs": True, "hills": "off", "snap_radius_m": 50},
        "design": "Fixed requested projected radii; equally spaced bearings; adjacent widths; ordered output; at most two concurrent planner calls",
        "native_binary_sha256": hashlib.sha256(Path(_routing_native.__file__).read_bytes()).hexdigest(),
    }
    statuses: Counter[str] = Counter()
    snapped_starts: dict[str, object] = {}
    manifest["snapped_starts"] = snapped_starts
    total = len(config.starts) * len(config.radii_m) * config.bearings * len(config.widths)
    completed = 0
    with gzip.open(raw_path, "wt", encoding="utf-8") as stream:
        for start in config.starts:
            # Release each start's bounded graph caches before moving to the next.
            with closing(SQLitePathData(database)) as data:
                if "dataset_id" in manifest and manifest["dataset_id"] != data.dataset_id:
                    raise ValueError("Dataset changed between starts")
                manifest["dataset_id"] = data.dataset_id
                manifest["metadata"] = asdict(data.metadata)
                snap = database_snap(data, start.longitude, start.latitude)["snap"]
                if snap is None:
                    raise ValueError(f"Start {start.name} has no eligible snap within 50 m")
                snapped_starts[start.name] = snap
                manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
                grid = GridConfig(home=(start.longitude, start.latitude), timeout_s=config.timeout_s)
                points = list(sweep_points(start, config))
                with ThreadPoolExecutor(max_workers=config.workers) as workers:
                    evaluate = partial(evaluate_sweep_point, data=data, grid=grid, limits=config.limits)
                    for point, sample in zip(points, workers.map(evaluate, points)):
                        response = sample.response
                        record = {"point": asdict(point), "wall_s": sample.wall_s, "error": sample.error,
                                  "response": None if response is None else response.model_dump(mode="json")}
                        stream.write(json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n")
                        statuses["planner_error" if response is None else response.status] += 1
                        if response is not None and "cost_model" not in manifest:
                            manifest["cost_model"] = response.cost_model.model_dump(mode="json")
                        completed += 1
                        if completed % 20 == 0 or completed == total:
                            stream.flush()
                            print(f"{completed}/{total} {start.name}: {dict(statuses)}; {perf_counter()-started:.1f}s", flush=True)
    manifest.update(complete=True, statuses=dict(statuses), evaluations=completed,
                    total_s=perf_counter() - started,
                    samples_sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--bearings", type=int, default=12)
    parser.add_argument("--radii", type=float, nargs="+", default=(1500, 3000))
    parser.add_argument("--widths", type=float, nargs="+", default=SweepConfig.widths)
    parser.add_argument("--timeout", type=float, default=3)
    parser.add_argument("--max-area-km2", type=float, default=2500)
    parser.add_argument("--max-segments", type=int, default=650000)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    config = SweepConfig(radii_m=tuple(args.radii), widths=tuple(args.widths),
                         bearings=args.bearings, timeout_s=args.timeout,
                         limits=RunAreaLimits(max_area_km2=args.max_area_km2, max_segments=args.max_segments),
                         workers=args.workers)
    sample_sweep(args.database, args.output, config)
    from runmap.experiments.plot_loop_width_sweep import analyze_sweep
    analyze_sweep(args.output)


if __name__ == "__main__":
    main()

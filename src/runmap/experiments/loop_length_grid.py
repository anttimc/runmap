"""Sample physical loop length over a direction-aligned turnaround/width grid."""

import argparse
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
from time import perf_counter
from typing import Literal

import numpy as np
from rasterio.warp import transform

from runmap.api.runs import EndpointRequest, PreferencesRequest, RunRequest, RunResponse, SettingsRequest
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_runs import RunAreaLimits, generate_database_run
from runmap.routing.cost_types import PreferenceLevel
from runmap.routing.run_types import RunSettings
from runmap.routing.settings import validate_settings


@dataclass(frozen=True)
class GridConfig:
    home: tuple[float, float] = (24.795, 60.216)
    bearing_deg: float = 0.0
    forward_m: tuple[float, float] = (300.0, 2500.0)
    lateral_m: tuple[float, float] = (-1500.0, 1500.0)
    forward_count: int = 20
    lateral_count: int = 20
    widths: tuple[float, ...] = (0.0, .06, .12, .18, .24, .30, .36, .42)
    hills: PreferenceLevel = "off"
    surface: Literal["all", "surfaced"] = "all"
    avoid_stairs: bool = True
    snap_radius_m: float = 50.0
    timeout_s: float = 15.0


@dataclass(frozen=True)
class GridPoint:
    sample_id: int
    forward_index: int
    lateral_index: int
    width_index: int
    forward_m: float
    lateral_m: float
    width: float
    longitude: float
    latitude: float


@dataclass(frozen=True)
class GridSample:
    point: GridPoint
    response: RunResponse | None
    wall_s: float
    error: str | None = None


def validate_config(config: GridConfig) -> None:
    values = (*config.home, config.bearing_deg, *config.forward_m, *config.lateral_m,
              config.snap_radius_m, config.timeout_s)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Grid coordinates and limits must be finite")
    if config.forward_count < 2 or config.lateral_count < 2:
        raise ValueError("Each spatial axis needs at least two points")
    if not (0 < config.forward_m[0] < config.forward_m[1]
            and config.lateral_m[0] < config.lateral_m[1]):
        raise ValueError("Grid bounds must be increasing, with positive forward distances")
    if not config.widths or any(a >= b for a, b in zip(config.widths, config.widths[1:])):
        raise ValueError("Width samples must be nonempty and strictly increasing")
    for width in config.widths:
        validate_settings(RunSettings(avoidance_ratio=width))
    if config.timeout_s <= 0:
        raise ValueError("Timeout must be positive")
    # Validate the same endpoint, policy, and snap constraints as the application.
    EndpointRequest(longitude=config.home[0], latitude=config.home[1])
    grid_request(config, "validation", next(iter_grid(config)))


def iter_grid(config: GridConfig) -> Iterator[GridPoint]:
    """Positive lateral distance is left of the bearing, measured from grid north."""
    home_x, home_y = transform("EPSG:4326", "EPSG:3067", [config.home[0]], [config.home[1]])
    angle = math.radians(config.bearing_deg)
    forward = np.linspace(*config.forward_m, config.forward_count)
    lateral = np.linspace(*config.lateral_m, config.lateral_count)
    positions = [(i, j, float(u), float(v)) for i, u in enumerate(forward) for j, v in enumerate(lateral)]
    xs = [home_x[0] + u * math.sin(angle) - v * math.cos(angle) for _, _, u, v in positions]
    ys = [home_y[0] + u * math.cos(angle) + v * math.sin(angle) for _, _, u, v in positions]
    lons, lats = transform("EPSG:3067", "EPSG:4326", xs, ys)
    sample_id = 0
    for (i, j, u, v), lon, lat in zip(positions, lons, lats):
        # Keep widths adjacent to share the planner's bounded working-set cache.
        for k, width in enumerate(config.widths):
            yield GridPoint(sample_id, i, j, k, u, v, width, lon, lat)
            sample_id += 1


def grid_request(config: GridConfig, dataset_id: str, point: GridPoint) -> RunRequest:
    return RunRequest(dataset_id=dataset_id,
        home=EndpointRequest(longitude=config.home[0], latitude=config.home[1]),
        turnaround=EndpointRequest(longitude=point.longitude, latitude=point.latitude),
        settings=SettingsRequest(avoidance_ratio=point.width),
        preferences=PreferencesRequest(hills=config.hills), surface=config.surface,
        avoid_stairs=config.avoid_stairs, radius_m=config.snap_radius_m)


def iter_samples(path: Path) -> Iterator[GridSample]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            response = None if record["response"] is None else RunResponse.model_validate(record["response"])
            yield GridSample(GridPoint(**record["point"]), response, float(record["wall_s"]), record.get("error"))


def evaluate_point(data: SQLitePathData, config: GridConfig, point: GridPoint,
                   limits: RunAreaLimits = RunAreaLimits()) -> GridSample:
    tick = perf_counter()
    try:
        response = generate_database_run(data, grid_request(config, data.dataset_id, point),
                                         timeout_s=config.timeout_s, limits=limits)
    except ValueError as error:
        # Preserve a reproducible failing input without assigning a made-up length.
        return GridSample(point, None, perf_counter() - tick, f"{type(error).__name__}: {error}")
    return GridSample(point, response, perf_counter() - tick)


def source_identity() -> dict[str, object]:
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout
    paths = [*Path("src/runmap").rglob("*.py"), *Path("rust/src").rglob("*.rs")]
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path).encode() + b"\0" + path.read_bytes() + b"\0")
    return {"revision": revision, "working_tree_status": status,
            "python_rust_source_sha256": digest.hexdigest()}


def run_grid(database: Path, output: Path, config: GridConfig) -> None:
    validate_config(config)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    samples_path = output / "samples.jsonl.gz"
    if manifest_path.exists() or samples_path.exists():
        raise FileExistsError("Use a fresh output directory to preserve previous samples")
    started = perf_counter()
    with closing(SQLitePathData(database)) as data:
        if data.numeric is None:
            raise ValueError("This experiment requires the matching numeric routing artifact")
        load_s = perf_counter() - started
        total = config.forward_count * config.lateral_count * len(config.widths)
        manifest: dict[str, object] = {
            "schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
            "complete": False, "config": asdict(config), "sample_count": total,
            "database": str(database.resolve()), "numeric_database": str(database.with_name("numeric-routing-v2.sqlite").resolve()),
            "dataset_id": data.dataset_id, "metadata": asdict(data.metadata),
            "source": source_identity(), "machine": platform.platform(), "python": platform.python_version(),
            "load_s": load_s, "run_area_limits": asdict(RunAreaLimits()),
            "coordinate_frame": "EPSG:3067; bearing clockwise from grid north; lateral positive left",
            "timing_scope": "Sequential in-process production API, including snapping/search/rendering; excludes serialization, startup and plotting",
            "sampling_order": "forward, lateral, width; widths share the bounded native graph cache",
            "limitations": "One start and fixed preferences; heuristic bounded routing; no fitted proxy or optimizer",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
        statuses: Counter[str] = Counter()
        times: list[float] = []
        with gzip.open(samples_path, "wt", encoding="utf-8") as stream:
            for point in iter_grid(config):
                sample = evaluate_point(data, config, point)
                response = sample.response
                record = {"point": asdict(point), "wall_s": sample.wall_s, "error": sample.error,
                          "response": None if response is None else response.model_dump(mode="json")}
                stream.write(json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n")
                statuses["planner_error" if response is None else response.status] += 1
                times.append(sample.wall_s)
                if "cost_model_example" not in manifest and response is not None:
                    manifest["cost_model_example"] = response.cost_model.model_dump(mode="json")
                if (point.sample_id + 1) % 100 == 0 or point.sample_id + 1 == total:
                    stream.flush()
                    print(f"{point.sample_id + 1}/{total}: {dict(statuses)}; elapsed {perf_counter() - started:.1f}s", flush=True)
        manifest.update(complete=True, statuses=dict(statuses), total_s=perf_counter() - started,
                        evaluation_p50_ms=float(np.percentile(times, 50) * 1000),
                        evaluation_p95_ms=float(np.percentile(times, 95) * 1000))
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--home", type=float, nargs=2, default=(24.795, 60.216), metavar=("LON", "LAT"))
    parser.add_argument("--bearing", type=float, default=0.0)
    parser.add_argument("--forward", type=float, nargs=2, default=(300, 2500), metavar=("MIN_M", "MAX_M"))
    parser.add_argument("--lateral", type=float, nargs=2, default=(-1500, 1500), metavar=("MIN_M", "MAX_M"))
    parser.add_argument("--grid", type=int, nargs=2, default=(20, 20), metavar=("FORWARD_COUNT", "LATERAL_COUNT"))
    parser.add_argument("--widths", type=float, nargs="+", default=GridConfig.widths)
    parser.add_argument("--hills", choices=("off", "low", "high"), default="off")
    parser.add_argument("--surface", choices=("all", "surfaced"), default="all")
    parser.add_argument("--allow-stairs", action="store_true")
    parser.add_argument("--snap-radius", type=float, default=50)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()
    config = GridConfig(home=tuple(args.home), bearing_deg=args.bearing, forward_m=tuple(args.forward),
        lateral_m=tuple(args.lateral), forward_count=args.grid[0], lateral_count=args.grid[1],
        widths=tuple(args.widths), hills=args.hills, surface=args.surface, avoid_stairs=not args.allow_stairs,
        snap_radius_m=args.snap_radius, timeout_s=args.timeout)
    run_grid(args.database, args.output, config)
    if not args.no_plots:
        from runmap.experiments.plot_loop_length_grid import plot_experiment
        plot_experiment(args.output)


if __name__ == "__main__":
    main()

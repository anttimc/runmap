"""Record reproducible local hill-preference tradeoffs without changing source data."""

import argparse
from dataclasses import asdict, dataclass
import json
import hashlib
from pathlib import Path
import platform
from time import perf_counter

from runmap.paths.measurements import segment_measurements
from runmap.api.paths import PathData, load_path_data
from runmap.api.runs import (EndpointRequest, LegResponse, PreferencesRequest, RunRequest,
                             SettingsRequest, generate_run)
from runmap.routing.cost_types import PreferenceLevel, PreferenceWeightTables, TraversalRef
from runmap.routing.hills import hill_observations, sum_hill_features, traversal_hills


@dataclass(frozen=True)
class LocalCase:
    name: str
    home: tuple[float, float]
    turnaround: tuple[float, float]
    home_segment: str | None = None
    turnaround_segment: str | None = None


CASES = (
    LocalCase("initial-outing", (24.795, 60.216), (24.815, 60.225)),
    LocalCase("school-directional-hill", (24.8050071, 60.2164463), (24.8058177, 60.2160201),
              "w642719209p1", "w642719209p1"),
    LocalCase("bridge-to-underpass", (24.8040928, 60.2164308), (24.806707, 60.2147618),
              "w171699102p0", "w16117809p0"),
    LocalCase("rolling-path", (24.798366, 60.214324), (24.7996492, 60.214344),
              "w575610022p0", "w575610022p0"),
)
LEVELS: tuple[PreferenceLevel, ...] = ("off", "low", "high")
STREET_CASES = (
    LocalCase("agnes-west-to-salmintie", (24.8014388, 60.2178992), (24.8034464, 60.2095759)),
    LocalCase("agnes-central-to-salmintie", (24.8031897, 60.217934), (24.8034464, 60.2095759)),
    LocalCase("agnes-east-to-salmintie", (24.8054122, 60.2178763), (24.8034464, 60.2095759)),
)


def slope_observations(data: PathData, leg: LegResponse) -> dict[str, float]:
    """Measure trusted uphill exposure independently of the optimization score."""
    above_five = above_ten = maximum = 0.0
    for traversal in leg.traversals:
        lo, hi = sorted((traversal.start_m, traversal.end_m))
        for item in segment_measurements(data.measurements, traversal.segment_id):
            overlap = min(hi, item["graph_end_m"]) - max(lo, item["graph_start_m"])
            grade = item["grade_percent"]
            if (overlap <= 0 or not item["valid"] or grade is None
                    or {"bridge", "tunnel", "missing_terrain"}.intersection(item["quality_flags"])):
                continue
            signed_grade = grade * (-1 if traversal.start_m > traversal.end_m else 1)
            length = overlap / (item["graph_end_m"] - item["graph_start_m"]) * (item["end_m"] - item["start_m"])
            above_five += length if signed_grade > 5 else 0.0
            above_ten += length if signed_grade > 10 else 0.0
            maximum = max(maximum, signed_grade)
    return {"trusted_uphill_above_5_percent_m": above_five,
            "trusted_uphill_above_10_percent_m": above_ten,
            "maximum_trusted_uphill_percent": maximum}


def benchmark(graph_path: Path, *, cases: tuple[LocalCase, ...] = CASES,
              widths: tuple[float, ...] = (0.0, .18)) -> dict[str, object]:
    started = perf_counter()
    data = load_path_data(graph_path)
    load_s = perf_counter() - started
    assert data.hills is not None
    rows: list[dict[str, object]] = []
    for case in cases:
        for width in widths:
            for level in LEVELS:
                options = RunRequest(
                    dataset_id=data.dataset_id,
                    home=EndpointRequest(longitude=case.home[0], latitude=case.home[1], segment_id=case.home_segment),
                    turnaround=EndpointRequest(longitude=case.turnaround[0], latitude=case.turnaround[1], segment_id=case.turnaround_segment),
                    settings=SettingsRequest(avoidance_ratio=width), preferences=PreferencesRequest(hills=level),
                )
                result = generate_run(data, options)
                candidates: list[dict[str, object]] = []
                for candidate in result.candidates:
                    legs: list[dict[str, object]] = []
                    for leg in (candidate.legs[0], candidate.legs[1]):
                        observed = sum_hill_features(traversal_hills(data.hills, TraversalRef(
                            item.segment_id, min(item.start_m, item.end_m), max(item.start_m, item.end_m),
                            item.start_m > item.end_m)) for item in leg.traversals)
                        legs.append({"distance_m": leg.distance_m, "cost": leg.cost,
                                     "cost_breakdown": asdict(leg.cost_breakdown), "observations": asdict(leg.observations),
                                     "comparison_terrain_observations": asdict(hill_observations(observed)),
                                     "comparison_slopes": slope_observations(data, leg),
                                     "expanded_nodes": leg.expanded_nodes,
                                     "traversals": [item.model_dump() for item in leg.traversals]})
                    candidates.append({"outbound_left": candidate.outbound_left,
                                       "distance_m": candidate.distance_m,
                                       "repeated_distance_m": candidate.repeated_distance_m,
                                       "outbound": legs[0], "returning": legs[1]})
                rows.append({"case": case.name, "request": options.model_dump(),
                             "status": result.status, "outcomes": result.outcomes,
                             "home": asdict(result.home) if result.home else None,
                             "turnaround": asdict(result.turnaround) if result.turnaround else None,
                             "elapsed_s": result.elapsed_s, "candidates": candidates})
    return {"schema_version": 1, "graph_path": str(graph_path), "dataset_id": data.dataset_id,
            "model": asdict(data.hills.model), "weight_tables": asdict(PreferenceWeightTables()),
            "feature_implementation_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                              for path in (Path("src/runmap/routing/hills.py"),
                                                           Path("src/runmap/routing/cost_settings.py"))},
            "graph_load_and_preparation_s": load_s,
            "numerical_terrain_coverage": data.hills.valid_fraction,
            "trusted_terrain_coverage": data.hills.trusted_fraction,
            "machine": platform.platform(), "python": platform.python_version(),
            "review_status": "Automated local observations; local runner acceptance pending.",
            "observation_scope": "Comparison terrain observations also evaluate selected Off routes after generation; Off search still skips hill feature work.",
            "timing_scope": "In-process API generation including snapping; excludes dataset load and HTTP/browser rendering.",
            "cases": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("output", type=Path)
    named_cases = {case.name: case for case in CASES + STREET_CASES}
    parser.add_argument("--case", action="append", choices=tuple(named_cases))
    parser.add_argument("--width", action="append", type=float)
    args = parser.parse_args()
    cases = tuple(named_cases[name] for name in args.case) if args.case else CASES
    widths = tuple(args.width) if args.width else (0.0, .18)
    args.output.write_text(json.dumps(benchmark(args.graph, cases=cases, widths=widths),
                                     indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()

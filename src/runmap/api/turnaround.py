"""Cache advisory turnaround shading independently of selected route endpoints."""

from dataclasses import replace
import json
import math
from time import monotonic

import numpy as np
from rasterio.warp import transform

from runmap.api.path_shells import PathShellService, ShellPayload, shell_features
from runmap.api.path_tiles import Snapshot, policy_options
from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_exploration import NumericExploration, retained_exploration_bytes
from runmap.paths.numeric_database import iter_numeric_payloads
from runmap.paths.numeric_format import decode_numeric_chunk
from runmap.routing.deadlines import check_deadline
from runmap.routing.distance_shells import (
    DistanceSamples, ShellSettings, build_distance_grid, distance_bands, merge_distance_samples,
)
from runmap.routing.numeric_search import numeric_distances
from runmap.routing.profiles import exploration_profile
from runmap.routing.turnaround import ellipse_extra_ratio, round_trip_samples


def load_round_trip_samples(service: PathShellService, snapshot: Snapshot, deadline: float) -> DistanceSamples:
    data = service.paths.data
    if not isinstance(data, SQLitePathData) or data.numeric is None or not isinstance(snapshot.local, NumericExploration):
        raise ValueError("Turnaround shading requires prepared numeric routing data")
    numeric = data.numeric
    profile = exploration_profile(*policy_options(snapshot.policy))
    graph, start = numeric.prepare_start(snapshot.local.bounds, snapshot.distances.snap, deadline)
    returning = numeric_distances(graph, start, snapshot.budget_km * 1000, profile=profile,
                                  reverse=True, deadline=deadline)
    if len(returning.frontier_node_ids):
        raise ValueError("Return search reached the loaded boundary; try a smaller target")
    if not numeric.lock.acquire(timeout=max(0, deadline - monotonic())):
        raise TimeoutError("Turnaround geometry is busy; try again")
    try:
        payloads = [stored.payload for stored in iter_numeric_payloads(numeric.connection, snapshot.local.bounds)]
    finally:
        numeric.lock.release()
    samples = []
    index = data.metadata.models.profiles.index(profile)
    for payload in payloads:
        check_deadline(deadline)
        samples.append(round_trip_samples(decode_numeric_chunk(payload), snapshot.local.distances.outward,
            returning, start, index, snapshot.budget_km * 1000))
    result = merge_distance_samples(samples)
    result.xy.setflags(write=False)
    result.distances.setflags(write=False)
    return result


def region_features(service: PathShellService, snapshot: Snapshot, samples: DistanceSamples,
                    target_m: float, width: float, deadline: float) -> list[dict[str, object]]:
    if not len(samples.xy):
        return []
    baseline = build_distance_grid(samples, ShellSettings(), deadline=deadline)
    snap = snapshot.distances.snap
    x, y = transform("EPSG:4326", "EPSG:3067", [snap.longitude], [snap.latitude])
    xx, yy = np.meshgrid(baseline.x, baseline.y)
    direct = np.hypot(xx - x[0], yy - y[0])
    estimated = baseline.values + direct * ellipse_extra_ratio(width)
    error = np.abs(estimated - target_m) / target_m
    error[baseline.values > target_m * 1.05] = np.nan
    grid = replace(baseline, values=error)
    bands = distance_bands(grid, 0.15, ShellSettings(interval_m=0.05))
    assert service.coverage is not None
    features, _ = shell_features(bands, service.coverage)
    for feature in features:
        properties = feature["properties"]
        assert isinstance(properties, dict)
        properties["core"] = properties["lower_m"] < 0.05
    return features


def encode_region(service: PathShellService, snapshot: Snapshot, target_m: float,
                  width: float, deadline: float) -> ShellPayload:
    if not service.workers.acquire(timeout=max(0, deadline - monotonic())):
        raise TimeoutError("Turnaround generation is busy; try again")
    try:
        samples, _ = service.samples.compute(f"turnaround:{snapshot.id}",
            lambda: load_round_trip_samples(service, snapshot, deadline), retained_exploration_bytes,
            deadline=deadline, allow_oversized=True)
        features = region_features(service, snapshot, samples, target_m, width, deadline)
        check_deadline(deadline)
        return ShellPayload(json.dumps({"type": "FeatureCollection", "features": features,
            "dataset_id": service.paths.data.dataset_id, "snapshot_id": snapshot.id,
            "target_m": target_m, "width": width, "tolerance": 0.05,
            "model": "ellipse-extra-v1"}, allow_nan=False).encode())
    finally:
        service.workers.release()


def render_region(service: PathShellService, snapshot: Snapshot, target_m: float, width: float) -> bytes:
    if not math.isfinite(target_m) or not 500 <= target_m <= 40000:
        raise ValueError("Target must be between 0.5 and 40 km")
    if not math.isfinite(width) or not 0 <= width <= 0.6:
        raise ValueError("Width must be between 0 and 0.6")
    if snapshot.budget_km * 1000 < target_m * 1.15:
        raise ValueError("Snapshot budget must cover 115% of the target")
    deadline = monotonic() + 20
    payload, _ = service.responses.compute(repr(("turnaround-v1", snapshot.id, target_m, width)),
        lambda: encode_region(service, snapshot, target_m, width, deadline),
        lambda value: len(value.encoded) + 256, deadline=deadline, allow_oversized=True)
    return payload.encoded

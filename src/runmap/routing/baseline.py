"""Anchor-based loop candidates using diverse outbound and return paths."""

import json
import math
from pathlib import Path

from runmap.routing.search import (
    CostModel,
    GraphIndex,
    route_coordinates,
    route_metrics,
    shortest_path,
    shortest_distances,
)


def select_loop_anchors(index, start, target_distance_m, count=24):
    """Choose spatially distributed vertices near useful half-loop distances."""
    if count < 1:
        raise ValueError("Anchor count must be positive")
    minimum = target_distance_m * 0.35
    maximum = target_distance_m * 0.55
    distances = shortest_distances(index, start, maximum=maximum)
    start_node = index.nodes[start]
    latitude_scale = math.cos(math.radians(start_node["latitude"]))
    candidates = []
    for node_id, distance in distances.items():
        if not minimum <= distance <= maximum or index.degree[node_id] < 2:
            continue
        node = index.nodes[node_id]
        east = (node["longitude"] - start_node["longitude"]) * latitude_scale
        north = node["latitude"] - start_node["latitude"]
        bearing = (math.degrees(math.atan2(east, north)) + 360) % 360
        candidates.append((node_id, distance, bearing))
    if not candidates:
        return []
    sector_count = max(1, min(count, 16))
    targets = (target_distance_m * 0.4, target_distance_m * 0.5)
    selected = []
    used = set()
    for target in targets:
        for sector in range(sector_count):
            west = sector * 360 / sector_count
            east = (sector + 1) * 360 / sector_count
            choices = [
                item for item in candidates
                if west <= item[2] < east and item[0] not in used
            ]
            if not choices:
                continue
            choice = min(choices, key=lambda item: abs(item[1] - target))
            selected.append(choice)
            used.add(choice[0])
            if len(selected) >= count:
                return selected
    return selected


def rank_candidate(candidate, target, tolerance, maximum_repeated_fraction):
    metrics = candidate["metrics"]
    distance_violation = max(
        target - tolerance - metrics["distance_m"],
        metrics["distance_m"] - target - tolerance,
        0,
    )
    repeat_violation = max(
        metrics["repeated_fraction"] - maximum_repeated_fraction, 0
    )
    feasible = distance_violation == 0 and repeat_violation == 0
    candidate["feasible"] = feasible
    candidate["constraint_violations"] = {
        "distance_m": round(distance_violation, 3),
        "repeated_fraction": round(repeat_violation, 6),
    }
    if feasible:
        return (
            0,
            metrics["ascent_m"],
            metrics["stair_distance_m"],
            abs(metrics["distance_m"] - target),
        )
    return (
        1,
        distance_violation,
        repeat_violation,
        metrics["ascent_m"],
        abs(metrics["distance_m"] - target),
    )


def generate_anchor_loops(index, request, settings):
    """Generate and rank loop alternatives for the first experiment."""
    start = index.document["start_snap"]["id"]
    target = float(request["target_distance_m"])
    tolerance = float(request["distance_tolerance_m"])
    maximum_repeated = float(request["max_repeated_fraction"])
    anchors = select_loop_anchors(
        index, start, target, int(settings.get("anchor_count", 24))
    )
    candidates = []
    seen = set()
    for anchor_id, anchor_distance, bearing in anchors:
        for ascent_weight in settings.get("ascent_costs_per_m", [20]):
            model = CostModel(
                ascent_cost_per_m=float(ascent_weight),
                stairs_multiplier=float(settings.get("stairs_multiplier", 8)),
            )
            outbound = shortest_path(index, start, anchor_id, model)
            if outbound is None:
                continue
            used = frozenset(
                index.arcs[arc_id]["segment_id"] for arc_id in outbound.arc_ids
            )
            for overlap_factor in settings.get("overlap_factors", [3, 10, 30]):
                returning = shortest_path(
                    index,
                    anchor_id,
                    start,
                    model,
                    overlap_segments=used,
                    overlap_factor=float(overlap_factor),
                )
                if returning is None:
                    continue
                arc_ids = outbound.arc_ids + returning.arc_ids
                identity = tuple(index.arcs[arc_id]["segment_id"] for arc_id in arc_ids)
                if identity in seen:
                    continue
                seen.add(identity)
                candidate = {
                    "anchor": {
                        "node_id": anchor_id,
                        "shortest_distance_from_start_m": round(anchor_distance, 3),
                        "bearing_degrees": round(bearing, 3),
                        "longitude": index.nodes[anchor_id]["longitude"],
                        "latitude": index.nodes[anchor_id]["latitude"],
                    },
                    "generation": {
                        "ascent_cost_per_m": float(ascent_weight),
                        "stairs_multiplier": model.stairs_multiplier,
                        "return_overlap_factor": float(overlap_factor),
                        "outbound_cost": round(outbound.cost, 3),
                        "return_cost": round(returning.cost, 3),
                    },
                    "outbound_arc_ids": list(outbound.arc_ids),
                    "return_arc_ids": list(returning.arc_ids),
                    "metrics": route_metrics(index, arc_ids),
                }
                candidate["_rank"] = rank_candidate(
                    candidate, target, tolerance, maximum_repeated
                )
                candidates.append(candidate)
    candidates.sort(key=lambda candidate: candidate["_rank"])
    alternative_count = int(settings.get("alternative_count", 5))
    selected = []
    selected_ids = set()
    used_anchors = set()
    feasible_candidates = [candidate for candidate in candidates if candidate["feasible"]]
    for candidate in feasible_candidates:
        anchor_id = candidate["anchor"]["node_id"]
        if anchor_id in used_anchors:
            continue
        selected.append(candidate)
        selected_ids.add(id(candidate))
        used_anchors.add(anchor_id)
        if len(selected) >= alternative_count:
            break
    if len(selected) < alternative_count:
        selected.extend(
            candidate for candidate in feasible_candidates
            if id(candidate) not in selected_ids
        )
        selected = selected[:alternative_count]
    if len(selected) < alternative_count:
        selected.extend(
            candidate for candidate in candidates
            if id(candidate) not in {id(item) for item in selected}
        )
        selected = selected[:alternative_count]
    for position, candidate in enumerate(selected, 1):
        candidate["id"] = f"alternative-{position}"
        del candidate["_rank"]
    return {
        "schema_version": 1,
        "method": "anchor-diverse-path-baseline",
        "start_node_id": start,
        "request": dict(request),
        "settings": dict(settings),
        "anchor_count": len(anchors),
        "evaluated_candidate_count": len(candidates),
        "feasible_candidate_count": sum(candidate["feasible"] for candidate in candidates),
        "boundary_blocked_segment_count": len(index.boundary_blocked_segments),
        "alternatives": selected,
    }


def routes_geojson(index, result):
    features = []
    for candidate in result["alternatives"]:
        arc_ids = candidate["outbound_arc_ids"] + candidate["return_arc_ids"]
        features.append({
            "type": "Feature",
            "id": candidate["id"],
            "geometry": {
                "type": "LineString",
                "coordinates": route_coordinates(index, arc_ids),
            },
            "properties": {
                "id": candidate["id"],
                "feasible": candidate["feasible"],
                **candidate["metrics"],
                **candidate["generation"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def write_route_outputs(index, result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        "routes": (directory / "diverse-baseline.json", result),
        "geojson": (directory / "diverse-baseline.geojson", routes_geojson(index, result)),
    }
    for path, document in outputs.values():
        partial = path.with_suffix(path.suffix + ".part")
        partial.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
        partial.replace(path)
    return {name: path for name, (path, _) in outputs.items()}

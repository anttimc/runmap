"""Anchor-based loop candidates using diverse outbound and return paths."""

from collections.abc import Iterator, Sequence
import json
import math
from pathlib import Path

from runmap.routing.profiles import exploration_profile, effective_edge_profile
from runmap.routing.search import (
    CostModel,
    GraphData,
    PathResult,
    apply_profile,
    route_coordinates,
    route_metrics,
    shortest_path,
    shortest_distances,
)

from runmap.routing.loop_types import (
    Anchor, Assessment, LoopCandidate, LoopRequest, LoopResult, LoopSettings, Rank, RankedCandidate,
)
from runmap.routing.types import RouteMetrics


def select_loop_anchors(
    index: GraphData, start: str, target_distance_m: float, count: int = 24,
) -> list[tuple[str, float, float]]:
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


def assess_candidate(metrics: RouteMetrics, request: LoopRequest) -> Assessment:
    distance_violation = max(
        request["target_distance_m"] - request["distance_tolerance_m"] - metrics["distance_m"],
        metrics["distance_m"] - request["target_distance_m"] - request["distance_tolerance_m"],
        0,
    )
    repeat_violation = max(metrics["repeated_fraction"] - request["max_repeated_fraction"], 0)
    return Assessment(distance_violation, repeat_violation, distance_violation == 0 and repeat_violation == 0)


def rank_candidate(metrics: RouteMetrics, assessment: Assessment, target: float) -> Rank:
    """Keep constraint failures ahead of ascent when comparing infeasible routes."""
    if assessment.feasible:
        return (0, metrics["ascent_m"], metrics["stair_distance_m"], abs(metrics["distance_m"] - target))
    return (
        1, assessment.distance_violation_m, assessment.repeated_fraction_violation,
        metrics["ascent_m"], abs(metrics["distance_m"] - target),
    )


def _assess_and_rank(candidate: LoopCandidate, request: LoopRequest) -> RankedCandidate:
    assessment = assess_candidate(candidate["metrics"], request)
    candidate["feasible"] = assessment.feasible
    candidate["constraint_violations"] = {
        "distance_m": round(assessment.distance_violation_m, 3),
        "repeated_fraction": round(assessment.repeated_fraction_violation, 6),
    }
    return RankedCandidate(candidate, rank_candidate(candidate["metrics"], assessment, request["target_distance_m"]))


def _build_candidate(
    index: GraphData, anchor: Anchor, model: CostModel, overlap_factor: float,
    outbound: PathResult, returning: PathResult,
) -> LoopCandidate:
    return {
        "anchor": {
            "node_id": anchor.node_id,
            "shortest_distance_from_start_m": round(anchor.distance_m, 3),
            "bearing_degrees": round(anchor.bearing_degrees, 3),
            "longitude": index.nodes[anchor.node_id]["longitude"],
            "latitude": index.nodes[anchor.node_id]["latitude"],
        },
        "generation": {
            "ascent_cost_per_m": model.ascent_cost_per_m,
            "stairs_multiplier": model.stairs_multiplier,
            "return_overlap_factor": overlap_factor,
            "outbound_cost": round(outbound.cost, 3), "return_cost": round(returning.cost, 3),
        },
        "outbound_arc_ids": list(outbound.arc_ids), "return_arc_ids": list(returning.arc_ids),
        "metrics": route_metrics(index, outbound.arc_ids + returning.arc_ids),
    }


def _anchor_candidates(
    index: GraphData, start: str, anchor: Anchor, settings: LoopSettings,
) -> Iterator[LoopCandidate]:
    for ascent_weight in settings.get("ascent_costs_per_m", [20]):
        model = CostModel(float(ascent_weight), float(settings.get("stairs_multiplier", 8)))
        outbound = shortest_path(index, start, anchor.node_id, model)
        if outbound is None:
            continue
        used = frozenset(index.arcs[arc_id]["segment_id"] for arc_id in outbound.arc_ids)
        for overlap_factor in settings.get("overlap_factors", [3, 10, 30]):
            returning = shortest_path(
                index, anchor.node_id, start, model,
                overlap_segments=used, overlap_factor=float(overlap_factor),
            )
            if returning is not None:
                yield _build_candidate(index, anchor, model, float(overlap_factor), outbound, returning)


def _generate_candidates(
    index: GraphData, start: str, anchors: Sequence[Anchor], request: LoopRequest, settings: LoopSettings,
) -> list[RankedCandidate]:
    candidates = []
    seen = set()
    for anchor in anchors:
        for candidate in _anchor_candidates(index, start, anchor, settings):
            arc_ids = candidate["outbound_arc_ids"] + candidate["return_arc_ids"]
            identity = tuple(index.arcs[arc_id]["segment_id"] for arc_id in arc_ids)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(_assess_and_rank(candidate, request))
    return sorted(candidates, key=lambda item: item.rank)


def _select_alternatives(candidates: Sequence[LoopCandidate], count: int) -> list[LoopCandidate]:
    """Prefer feasible routes from distinct anchors before filling remaining slots."""
    selected = []
    selected_ids = set()
    used_anchors = set()
    feasible = [candidate for candidate in candidates if candidate["feasible"]]
    for candidate in feasible:
        anchor_id = candidate["anchor"]["node_id"]
        if anchor_id in used_anchors:
            continue
        selected.append(candidate)
        selected_ids.add(id(candidate))
        used_anchors.add(anchor_id)
        if len(selected) >= count:
            break
    if len(selected) < count:
        selected.extend(candidate for candidate in feasible if id(candidate) not in selected_ids)
        selected = selected[:count]
    if len(selected) < count:
        selected.extend(candidate for candidate in candidates if id(candidate) not in {id(item) for item in selected})
        selected = selected[:count]
    return selected


def _loop_index(index: GraphData, settings: LoopSettings) -> GraphData:
    policy = exploration_profile(avoid_stairs=False)
    policy.update(settings.get("edge_profile") or {})
    policy = effective_edge_profile(policy, settings.get("stairs", {"mode": "penalize"}))
    return apply_profile(index, policy)


def generate_anchor_loops(index: GraphData, request: LoopRequest, settings: LoopSettings) -> LoopResult:
    """Explore varied ascent and overlap penalties, then retain diverse alternatives."""
    index = _loop_index(index, settings)
    start = index.document["start_snap"]["id"]
    normalized: LoopRequest = {
        "target_distance_m": float(request["target_distance_m"]),
        "distance_tolerance_m": float(request["distance_tolerance_m"]),
        "max_repeated_fraction": float(request["max_repeated_fraction"]),
    }
    anchors = [Anchor(*item) for item in select_loop_anchors(
        index, start, normalized["target_distance_m"], int(settings.get("anchor_count", 24)),
    )]
    ranked = _generate_candidates(index, start, anchors, normalized, settings)
    candidates = [item.candidate for item in ranked]
    selected = _select_alternatives(candidates, int(settings.get("alternative_count", 5)))
    for position, candidate in enumerate(selected, 1):
        candidate["id"] = f"alternative-{position}"
    return {
        "schema_version": 1, "method": "anchor-diverse-path-baseline", "start_node_id": start,
        "request": request.copy(), "settings": settings.copy(), "anchor_count": len(anchors),
        "evaluated_candidate_count": len(candidates),
        "feasible_candidate_count": sum(candidate["feasible"] for candidate in candidates),
        "boundary_blocked_segment_count": len(index.boundary_blocked_segments), "alternatives": selected,
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

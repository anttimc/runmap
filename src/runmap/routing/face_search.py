"""Experimental loop search by growing connected planar face regions."""

from collections import defaultdict
import json
import math
from pathlib import Path
import random
import time

from runmap.routing.baseline import rank_candidate
from runmap.routing.search import (
    CostModel,
    route_coordinates,
    route_metrics,
    shortest_distances,
    shortest_path,
)


def _toggle_boundary(boundary, segment_ids):
    result = set(boundary)
    for segment_id in segment_ids:
        if segment_id in result:
            result.remove(segment_id)
        else:
            result.add(segment_id)
    return result


def ordered_boundary(index, segment_ids, preferred_nodes=()):
    """Order one undirected degree-two boundary, or return None."""
    segment_ids = set(segment_ids)
    if not segment_ids:
        return None
    adjacency = defaultdict(list)
    for segment_id in segment_ids:
        segment = index.segments[segment_id]
        first, second = segment["from"], segment["to"]
        adjacency[first].append((segment_id, second))
        adjacency[second].append((segment_id, first))
    if any(len(edges) != 2 for edges in adjacency.values()):
        return None
    preferred = [node_id for node_id in preferred_nodes if node_id in adjacency]
    start = preferred[0] if preferred else min(adjacency)
    ordered = []
    used_segments = set()
    node_id = start
    previous_segment = None
    while True:
        choices = [item for item in adjacency[node_id] if item[0] != previous_segment]
        if not choices:
            return None
        segment_id, neighbour = min(choices)
        if segment_id in used_segments:
            return ordered if neighbour == start and len(ordered) == len(segment_ids) else None
        ordered.append((segment_id, node_id, neighbour))
        used_segments.add(segment_id)
        previous_segment = segment_id
        node_id = neighbour
        if node_id == start:
            return ordered if len(ordered) == len(segment_ids) else None


def _arc_ids(index, ordered):
    by_traversal = {
        (arc["segment_id"], arc["from"], arc["to"]): arc_id
        for arc_id, arc in index.arcs.items()
    }
    result = []
    for segment_id, first, second in ordered:
        arc_id = by_traversal.get((segment_id, first, second))
        if arc_id is None:
            return None
        result.append(arc_id)
    return tuple(result)


def _reverse_order(ordered):
    return [(segment_id, second, first) for segment_id, first, second in reversed(ordered)]


def _reverse_arcs(index, arc_ids):
    traversals = {
        (arc["segment_id"], arc["from"], arc["to"]): arc_id
        for arc_id, arc in index.arcs.items()
    }
    result = []
    for arc_id in reversed(arc_ids):
        arc = index.arcs[arc_id]
        reverse_id = traversals.get((arc["segment_id"], arc["to"], arc["from"]))
        if reverse_id is None:
            return None
        result.append(reverse_id)
    return tuple(result)


def _face_adjacency(embedding):
    adjacent = defaultdict(set)
    for item in embedding["face_adjacency"]:
        first, second = item["faces"]
        adjacent[first].add(second)
        adjacent[second].add(first)
    return adjacent


def _face_centroid(face):
    coordinates = face["geometry"][:-1] or face["geometry"]
    return (
        sum(point[0] for point in coordinates) / len(coordinates),
        sum(point[1] for point in coordinates) / len(coordinates),
    )


def _build_candidate(
    index, face_by_id, faces, boundary, distances, start, model,
    overlap_factor, access_cache,
):
    reachable_nodes = sorted(
        {
            node_id
            for segment_id in boundary
            for node_id in (
                index.segments[segment_id]["from"], index.segments[segment_id]["to"]
            )
            if node_id in distances
        },
        key=lambda node_id: distances[node_id],
    )
    ordered = ordered_boundary(index, boundary, reachable_nodes)
    if ordered is None:
        return None
    attachment = ordered[0][1]
    if attachment not in access_cache:
        access_cache[attachment] = shortest_path(index, start, attachment, model)
    access = access_cache[attachment]
    if access is None:
        return None
    access_segments = {
        index.arcs[arc_id]["segment_id"] for arc_id in access.arc_ids
    }
    cycle_options = []
    for direction in (ordered, _reverse_order(ordered)):
        cycle_arc_ids = _arc_ids(index, direction)
        if cycle_arc_ids is None:
            continue
        cycle_options.append((
            sum(float(index.arcs[arc_id].get("ascent_m", 0)) for arc_id in cycle_arc_ids),
            cycle_arc_ids,
        ))
    if not cycle_options:
        return None
    _, cycle_arc_ids = min(cycle_options)
    return_arc_ids = _reverse_arcs(index, access.arc_ids)
    if return_arc_ids is None:
        used = access_segments | {
            index.arcs[arc_id]["segment_id"] for arc_id in cycle_arc_ids
        }
        returning = shortest_path(
            index,
            attachment,
            start,
            model,
            overlap_segments=used,
            overlap_factor=overlap_factor,
        )
        if returning is None:
            return None
        return_arc_ids = returning.arc_ids
    arc_ids = access.arc_ids + cycle_arc_ids + return_arc_ids
    metrics = route_metrics(index, arc_ids)
    return {
        "face_ids": sorted(faces),
        "face_count": len(faces),
        "region_area_m2": round(
            sum(face_by_id[face_id]["area_m2"] for face_id in faces), 3
        ),
        "boundary_segment_ids": sorted(boundary),
        "attachment_node_id": attachment,
        "access_arc_ids": list(access.arc_ids),
        "boundary_arc_ids": list(cycle_arc_ids),
        "return_arc_ids": list(return_arc_ids),
        "metrics": metrics,
    }


def generate_face_region_loops(index, topology, request, settings=None):
    """Grow deterministic randomized face regions and rank usable boundaries."""
    started = time.perf_counter()
    settings = dict(settings or {})
    embedding = topology["face_embedding"]
    face = {item["id"]: item for item in embedding["faces"]}
    adjacent = _face_adjacency(embedding)
    start = index.document["start_snap"]["id"]
    target = float(request["target_distance_m"])
    tolerance = float(request["distance_tolerance_m"])
    max_repeat = float(request["max_repeated_fraction"])
    seed_count = int(settings.get("seed_count", 12))
    variants = int(settings.get("variants_per_seed", 6))
    max_faces = int(settings.get("max_faces", 500))
    maximum_candidates = int(settings.get("maximum_candidates", 250))
    maximum_boundary_overlap = float(settings.get("maximum_boundary_overlap", 0.8))
    overlap_factor = float(settings.get("access_overlap_factor", 10))
    model = CostModel(
        ascent_cost_per_m=float(settings.get("ascent_cost_per_m", 20)),
        stairs_multiplier=float(settings.get("stairs_multiplier", 8)),
    )
    distances = shortest_distances(index, start)
    seeds = []
    for item in face.values():
        reachable = [distances[node_id] for node_id in item["node_ids"] if node_id in distances]
        if reachable:
            seeds.append((min(reachable), item["id"]))
    seeds.sort()
    seeds = seeds[:seed_count]
    centroids = {face_id: _face_centroid(item) for face_id, item in face.items()}
    candidates = []
    access_cache = {}
    seen_boundaries = set()
    attempted_regions = 0
    search_count = max(1, len(seeds) * variants)
    candidates_per_search, extra_candidates = divmod(maximum_candidates, search_count)
    for seed_offset, (_, seed_id) in enumerate(seeds):
        seed_centroid = centroids[seed_id]
        for variant in range(variants):
            search_offset = seed_offset * variants + variant
            candidate_limit = candidates_per_search + (search_offset < extra_candidates)
            if candidate_limit == 0:
                continue
            variant_candidate_count = 0
            rng = random.Random(seed_offset * 1009 + variant * 9173 + 41)
            selected = {seed_id}
            boundary = set(face[seed_id]["segment_ids"])
            for _ in range(max_faces):
                boundary_length = sum(float(index.segments[s]["length_m"]) for s in boundary)
                if target - 2 * tolerance <= boundary_length <= target + tolerance:
                    identity = frozenset(boundary)
                    if identity not in seen_boundaries:
                        seen_boundaries.add(identity)
                        attempted_regions += 1
                        candidate = _build_candidate(
                            index, face, selected, boundary, distances, start,
                            model, overlap_factor, access_cache,
                        )
                        if candidate is not None:
                            candidate["generation"] = {
                                "seed_face_id": seed_id,
                                "variant": variant,
                                "ascent_cost_per_m": model.ascent_cost_per_m,
                                "stairs_multiplier": model.stairs_multiplier,
                                "access_overlap_factor": overlap_factor,
                            }
                            candidate["_rank"] = rank_candidate(
                                candidate, target, tolerance, max_repeat
                            )
                            candidates.append(candidate)
                            variant_candidate_count += 1
                            if variant_candidate_count >= candidate_limit:
                                break
                frontier = {
                    neighbour
                    for face_id in selected
                    for neighbour in adjacent.get(face_id, ())
                    if neighbour not in selected
                }
                choices = []
                for next_id in sorted(frontier):
                    trial = _toggle_boundary(boundary, face[next_id]["segment_ids"])
                    trial_length = sum(float(index.segments[s]["length_m"]) for s in trial)
                    lon, lat = centroids[next_id]
                    distance_from_seed = math.hypot(
                        (lon - seed_centroid[0]) * 55000,
                        (lat - seed_centroid[1]) * 111000,
                    )
                    compactness = (variant % 3) * 0.15 * distance_from_seed
                    jitter = rng.random() * (variant // 3 + 1) * 250
                    choices.append((abs(target - trial_length) + compactness + jitter, next_id, trial))
                if not choices:
                    break
                chosen = next(
                    (choice for choice in sorted(choices) if ordered_boundary(index, choice[2]) is not None),
                    None,
                )
                if chosen is None:
                    break
                _, next_id, boundary = chosen
                selected.add(next_id)
    candidates.sort(key=lambda item: item["_rank"])
    alternative_count = int(settings.get("alternative_count", 5))
    selected_candidates = []
    selected_ids = set()
    for candidate in candidates:
        boundary = set(candidate["boundary_segment_ids"])
        if any(
            len(boundary & set(selected["boundary_segment_ids"]))
            / len(boundary | set(selected["boundary_segment_ids"]))
            > maximum_boundary_overlap
            for selected in selected_candidates
        ):
            continue
        selected_candidates.append(candidate)
        selected_ids.add(id(candidate))
        if len(selected_candidates) >= alternative_count:
            break
    if len(selected_candidates) < alternative_count:
        selected_candidates.extend(
            candidate for candidate in candidates if id(candidate) not in selected_ids
        )
        selected_candidates = selected_candidates[:alternative_count]
    for offset, candidate in enumerate(selected_candidates, 1):
        candidate["id"] = f"face-alternative-{offset}"
        del candidate["_rank"]
    return {
        "schema_version": 1,
        "method": "connected-face-region-boundary",
        "start_node_id": start,
        "request": dict(request),
        "settings": settings,
        "seed_face_ids": [face_id for _, face_id in seeds],
        "attempted_region_count": attempted_regions,
        "evaluated_candidate_count": len(candidates),
        "feasible_candidate_count": sum(item["feasible"] for item in candidates),
        "runtime_seconds": round(time.perf_counter() - started, 3),
        "alternatives": selected_candidates,
    }


def face_routes_geojson(index, result):
    features = []
    for candidate in result["alternatives"]:
        arc_ids = (
            candidate["access_arc_ids"]
            + candidate["boundary_arc_ids"]
            + candidate["return_arc_ids"]
        )
        features.append({
            "type": "Feature",
            "id": candidate["id"],
            "geometry": {"type": "LineString", "coordinates": route_coordinates(index, arc_ids)},
            "properties": {
                "id": candidate["id"],
                "feasible": candidate["feasible"],
                "face_count": candidate["face_count"],
                "region_area_m2": candidate["region_area_m2"],
                **candidate["metrics"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def write_face_route_outputs(index, result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        "routes": (directory / "face-regions.json", result),
        "geojson": (directory / "face-regions.geojson", face_routes_geojson(index, result)),
    }
    for path, document in outputs.values():
        partial = path.with_suffix(path.suffix + ".part")
        partial.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
        partial.replace(path)
    return {name: path for name, (path, _) in outputs.items()}

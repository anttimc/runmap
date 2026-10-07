"""Experimental loop search by growing connected planar face regions."""

from collections import defaultdict
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import random
import time

from runmap.routing.baseline import rank_candidate
from runmap.routing.profiles import dissolve_forbidden_separators
from runmap.routing.search import (
    CostModel,
    route_coordinates,
    route_metrics,
    shortest_distances,
    shortest_path,
)


@dataclass(frozen=True)
class _FaceSearchSettings:
    target: float
    tolerance: float
    maximum_repeated: float
    seed_count: int
    alternative_count: int
    maximum_boundary_overlap: float
    turns: dict
    stairs: dict
    sharp_threshold: float
    maximum_faces: int
    overlap_factor: float
    model: CostModel
    variants: int
    maximum_candidates: int
    beam_width: int
    maximum_chunk_size: int
    chunk_proposals_per_size: int
    growth_shape_weight: float
    near_reversal_weight: float
    growth_jitter: float


@dataclass(frozen=True)
class _FaceSearchData:
    index: object
    face_by_id: dict
    adjacent: dict
    distances: dict
    start: str
    blocked_segments: frozenset


@dataclass(frozen=True)
class _FaceSearchResults:
    candidates: list
    attempted_regions: int
    expanded_state_count: int
    proposed_chunk_count: int


@dataclass
class _FaceSearchWorkspace:
    candidates: list = field(default_factory=list)
    access_cache: dict = field(default_factory=dict)
    seen_boundaries: set = field(default_factory=set)
    attempted_regions: int = 0
    expanded_state_count: int = 0
    proposed_chunk_count: int = 0


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


def boundary_shape_metrics(index, ordered, region_area_m2, sharp_threshold=170):
    """Measure junction turns and geometric compactness of one closed boundary."""
    nodes = [item[1] for item in ordered]
    bearings = []
    for first, second in zip(nodes, nodes[1:] + nodes[:1]):
        origin = index.nodes[first]
        destination = index.nodes[second]
        east = (destination["longitude"] - origin["longitude"]) * math.cos(
            math.radians(origin["latitude"])
        )
        north = destination["latitude"] - origin["latitude"]
        bearings.append(math.degrees(math.atan2(east, north)))
    turns = [
        (second - first + 180) % 360 - 180
        for first, second in zip(bearings, bearings[1:] + bearings[:1])
    ]
    dominant = 1 if sum(turns) >= 0 else -1
    meaningful = [turn for turn in turns if abs(turn) >= 15]
    concave = sum(turn * dominant < 0 for turn in meaningful)
    sharp = sum(abs(turn) >= sharp_threshold for turn in turns)
    perimeter = sum(float(index.segments[item[0]]["length_m"]) for item in ordered)
    compactness = (
        perimeter ** 2 / (4 * math.pi * region_area_m2)
        if region_area_m2 > 0 else math.inf
    )
    return {
        "junction_turn_count": len(meaningful),
        "concave_turn_count": concave,
        "sharp_turn_count": sharp,
        "maximum_turn_degrees": round(max(map(abs, turns), default=0), 3),
        "turn_penalty": round(sum((abs(turn) / 90) ** 2 for turn in turns), 6),
        "compactness": round(compactness, 6),
    }


def _shape_score(shape, turns):
    return (
        shape["concave_turn_count"] * float(turns.get("concave_turn_weight", 30))
        + shape["turn_penalty"] * float(turns.get("turn_weight", 1))
        + max(shape["compactness"] - 1, 0)
        * float(turns.get("compactness_weight", 10))
    )


def _growth_chunks(selected, adjacent, maximum_size, proposals_per_size, rng):
    """Propose bounded connected chunks attached to a selected face region."""
    frontier = sorted({
        neighbour
        for face_id in selected
        for neighbour in adjacent.get(face_id, ())
        if neighbour not in selected
    })
    rng.shuffle(frontier)
    layer = [frozenset({face_id}) for face_id in frontier[:proposals_per_size]]
    chunks = list(layer)
    for _ in range(2, maximum_size + 1):
        expanded = set()
        for chunk in layer:
            additions = sorted({
                neighbour
                for face_id in chunk
                for neighbour in adjacent.get(face_id, ())
                if neighbour not in selected and neighbour not in chunk
            })
            rng.shuffle(additions)
            for neighbour in additions:
                expanded.add(chunk | {neighbour})
        layer = sorted(expanded, key=lambda item: tuple(sorted(item)))
        rng.shuffle(layer)
        layer = layer[:proposals_per_size]
        chunks.extend(layer)
        if not layer:
            break
    return chunks


def _growth_state(
    index, face_by_id, selected, boundary, target, turns, sharp_threshold,
    shape_weight, near_reversal_weight, jitter,
):
    ordered = ordered_boundary(index, boundary)
    if ordered is None:
        return None
    area = sum(float(face_by_id[face_id]["area_m2"]) for face_id in selected)
    shape = boundary_shape_metrics(index, ordered, area, sharp_threshold)
    perimeter = sum(float(index.segments[item[0]]["length_m"]) for item in ordered)
    return {
        "selected": frozenset(selected),
        "boundary": frozenset(boundary),
        "ordered": ordered,
        "area_m2": area,
        "boundary_length_m": perimeter,
        "shape_metrics": shape,
        "growth_rank": (
            abs(target - perimeter)
            + shape_weight * _shape_score(shape, turns)
            + shape["sharp_turn_count"] * near_reversal_weight
            + jitter
        ),
    }


def _rank_face_candidate(candidate, settings):
    base_rank = rank_candidate(
        candidate,
        settings.target,
        settings.tolerance,
        settings.maximum_repeated,
    )
    turns = settings.turns
    shape = candidate["shape_metrics"]
    maximum_sharp = int(turns.get("maximum_sharp_turns", 0))
    sharp_violation = max(shape["sharp_turn_count"] - maximum_sharp, 0)
    candidate["constraint_violations"]["sharp_turn_count"] = sharp_violation
    if sharp_violation:
        candidate["feasible"] = False
    shape_score = _shape_score(shape, turns)
    stairs = settings.stairs
    if stairs.get("mode", "avoid") == "penalize":
        shape_score += candidate["metrics"]["stair_distance_m"] * float(
            stairs.get("penalty_per_m", 10)
        )
    candidate["shape_score"] = round(shape_score, 6)
    if candidate["feasible"]:
        return (
            0,
            candidate["shape_score"],
            candidate["metrics"]["ascent_m"],
            abs(candidate["metrics"]["distance_m"] - settings.target),
        )
    return (1, sharp_violation, *base_rank[1:], candidate["shape_score"])


def _build_candidate(
    data, settings, faces, boundary, access_cache,
):
    index = data.index
    reachable_nodes = sorted(
        {
            node_id
            for segment_id in boundary
            for node_id in (
                index.segments[segment_id]["from"], index.segments[segment_id]["to"]
            )
            if node_id in data.distances
        },
        key=lambda node_id: data.distances[node_id],
    )
    ordered = ordered_boundary(index, boundary, reachable_nodes)
    if ordered is None:
        return None
    attachment = ordered[0][1]
    if attachment not in access_cache:
        access_cache[attachment] = shortest_path(
            index,
            data.start,
            attachment,
            settings.model,
            blocked_segments=data.blocked_segments,
        )
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
            data.start,
            settings.model,
            overlap_segments=used,
            overlap_factor=settings.overlap_factor,
            blocked_segments=data.blocked_segments,
        )
        if returning is None:
            return None
        return_arc_ids = returning.arc_ids
    arc_ids = access.arc_ids + cycle_arc_ids + return_arc_ids
    metrics = route_metrics(index, arc_ids)
    region_area = sum(data.face_by_id[face_id]["area_m2"] for face_id in faces)
    base_face_ids = sorted({
        base_face_id
        for face_id in faces
        for base_face_id in data.face_by_id[face_id].get(
            "member_face_ids", [face_id]
        )
    })
    return {
        "face_ids": sorted(faces),
        "face_count": len(faces),
        "base_face_ids": base_face_ids,
        "base_face_count": len(base_face_ids),
        "region_area_m2": round(
            region_area, 3
        ),
        "shape_metrics": boundary_shape_metrics(
            index, ordered, region_area, settings.sharp_threshold
        ),
        "boundary_segment_ids": sorted(boundary),
        "attachment_node_id": attachment,
        "access_arc_ids": list(access.arc_ids),
        "boundary_arc_ids": list(cycle_arc_ids),
        "return_arc_ids": list(return_arc_ids),
        "metrics": metrics,
    }


def _prepare_face_embedding(index, topology, settings):
    """Apply route restrictions and return the searchable face embedding."""
    profile = settings.get("edge_profile")
    stairs_mode = settings.get("stairs", {}).get("mode", "avoid")
    if stairs_mode not in {"avoid", "penalize", "allow"}:
        raise ValueError("stairs.mode must be avoid, penalize, or allow")
    if stairs_mode == "avoid":
        profile = dict(profile or {"id": "default"})
        profile["forbidden_highways"] = sorted(
            set(profile.get("forbidden_highways", ())) | {"steps"}
        )
    if not profile:
        return topology["face_embedding"], frozenset(), None
    embedding = dissolve_forbidden_separators(
        index, topology["face_embedding"], profile
    )
    return (
        embedding,
        frozenset(embedding["forbidden_segment_ids"]),
        embedding["summary"],
    )


def _reachable_face_seeds(face_by_id, distances, limit):
    seeds = []
    for face in face_by_id.values():
        reachable = [
            distances[node_id]
            for node_id in face["node_ids"]
            if node_id in distances
        ]
        if reachable:
            seeds.append((min(reachable), face["id"]))
    return sorted(seeds)[:limit]


def _boundary_with_faces(boundary, face_by_id, face_ids):
    boundary = set(boundary)
    for face_id in sorted(face_ids):
        boundary = _toggle_boundary(boundary, face_by_id[face_id]["segment_ids"])
    return boundary


def _parse_face_search_settings(request, settings):
    growth = settings.get("growth", {})
    parsed = _FaceSearchSettings(
        target=float(request["target_distance_m"]),
        tolerance=float(request["distance_tolerance_m"]),
        maximum_repeated=float(request["max_repeated_fraction"]),
        seed_count=int(settings.get("seed_count", 12)),
        alternative_count=int(settings.get("alternative_count", 5)),
        maximum_boundary_overlap=float(
            settings.get("maximum_boundary_overlap", 0.8)
        ),
        turns=settings.get("turns", {}),
        stairs=settings.get("stairs", {}),
        sharp_threshold=float(
            settings.get("turns", {}).get("sharp_threshold_degrees", 170)
        ),
        maximum_faces=int(settings.get("max_faces", 500)),
        overlap_factor=float(settings.get("access_overlap_factor", 10)),
        model=CostModel(
            ascent_cost_per_m=float(settings.get("ascent_cost_per_m", 20)),
            stairs_multiplier=float(settings.get("stairs_multiplier", 8)),
        ),
        variants=int(settings.get("variants_per_seed", 6)),
        maximum_candidates=int(settings.get("maximum_candidates", 250)),
        beam_width=int(growth.get("beam_width", 3)),
        maximum_chunk_size=int(growth.get("maximum_chunk_size", 3)),
        chunk_proposals_per_size=int(
            growth.get("chunk_proposals_per_size", 24)
        ),
        growth_shape_weight=float(growth.get("shape_weight", 1)),
        near_reversal_weight=float(growth.get("near_reversal_weight", 750)),
        growth_jitter=float(growth.get("jitter", 75)),
    )
    if (
        parsed.beam_width < 1
        or parsed.maximum_chunk_size < 1
        or parsed.chunk_proposals_per_size < 1
    ):
        raise ValueError("growth beam and chunk settings must be positive")
    return parsed


def _make_growth_state(data, settings, selected, boundary, jitter):
    return _growth_state(
        data.index,
        data.face_by_id,
        selected,
        boundary,
        settings.target,
        settings.turns,
        settings.sharp_threshold,
        settings.growth_shape_weight,
        settings.near_reversal_weight,
        jitter,
    )


def _candidate_from_state(
    data, settings, workspace, state, seed_id, variant,
):
    boundary_length = state["boundary_length_m"]
    if not (
        settings.target - 2 * settings.tolerance
        <= boundary_length
        <= settings.target + settings.tolerance
    ):
        return None
    identity = state["boundary"]
    if identity in workspace.seen_boundaries:
        return None
    workspace.seen_boundaries.add(identity)
    workspace.attempted_regions += 1
    candidate = _build_candidate(
        data,
        settings,
        state["selected"],
        state["boundary"],
        workspace.access_cache,
    )
    if candidate is None:
        return None
    candidate["generation"] = {
        "seed_face_id": seed_id,
        "variant": variant,
        "chunk_sizes": state["chunk_sizes"],
        "beam_width": settings.beam_width,
        "ascent_cost_per_m": settings.model.ascent_cost_per_m,
        "stairs_multiplier": settings.model.stairs_multiplier,
        "access_overlap_factor": settings.overlap_factor,
    }
    candidate["_rank"] = _rank_face_candidate(candidate, settings)
    return candidate


def _expand_growth_states(
    data, settings, workspace, states, visited_regions, rng, variant,
):
    proposals = []
    for state in states:
        workspace.expanded_state_count += 1
        chunks = _growth_chunks(
            state["selected"],
            data.adjacent,
            settings.maximum_chunk_size,
            settings.chunk_proposals_per_size,
            rng,
        )
        workspace.proposed_chunk_count += len(chunks)
        for chunk in chunks:
            selected = state["selected"] | chunk
            if (
                len(selected) > settings.maximum_faces
                or selected in visited_regions
            ):
                continue
            boundary = _boundary_with_faces(
                state["boundary"], data.face_by_id, chunk
            )
            proposed = _make_growth_state(
                data,
                settings,
                selected,
                boundary,
                rng.random() * settings.growth_jitter * (variant + 1),
            )
            if proposed is None:
                continue
            proposed["chunk_sizes"] = state["chunk_sizes"] + [len(chunk)]
            proposals.append(proposed)

    next_states = []
    for proposed in sorted(proposals, key=lambda item: item["growth_rank"]):
        if proposed["selected"] in visited_regions:
            continue
        visited_regions.add(proposed["selected"])
        next_states.append(proposed)
        if len(next_states) >= settings.beam_width:
            break
    return next_states


def _run_search_variant(
    data, settings, workspace, seed_offset, seed_id, variant, candidate_limit,
):
    rng = random.Random(seed_offset * 1009 + variant * 9173 + 41)
    initial = _make_growth_state(
        data,
        settings,
        {seed_id},
        data.face_by_id[seed_id]["segment_ids"],
        jitter=0,
    )
    if initial is None:
        return
    initial["chunk_sizes"] = []
    states = [initial]
    visited_regions = {initial["selected"]}
    candidate_count = 0
    for _ in range(settings.maximum_faces):
        for state in states:
            candidate = _candidate_from_state(
                data, settings, workspace, state, seed_id, variant
            )
            if candidate is None:
                continue
            workspace.candidates.append(candidate)
            candidate_count += 1
            if candidate_count >= candidate_limit:
                return
        states = _expand_growth_states(
            data,
            settings,
            workspace,
            states,
            visited_regions,
            rng,
            variant,
        )
        if not states:
            return


def _search_face_regions(data, settings, seeds):
    """Search face regions using the supplied graph data and settings."""
    workspace = _FaceSearchWorkspace()
    search_count = max(1, len(seeds) * settings.variants)
    candidates_per_search, extra_candidates = divmod(
        settings.maximum_candidates, search_count
    )
    for seed_offset, (_, seed_id) in enumerate(seeds):
        for variant in range(settings.variants):
            search_offset = seed_offset * settings.variants + variant
            candidate_limit = candidates_per_search + (
                search_offset < extra_candidates
            )
            if candidate_limit:
                _run_search_variant(
                    data,
                    settings,
                    workspace,
                    seed_offset,
                    seed_id,
                    variant,
                    candidate_limit,
                )
    candidates = sorted(
        workspace.candidates, key=lambda candidate: candidate["_rank"]
    )
    return _FaceSearchResults(
        candidates=candidates,
        attempted_regions=workspace.attempted_regions,
        expanded_state_count=workspace.expanded_state_count,
        proposed_chunk_count=workspace.proposed_chunk_count,
    )


def _append_unselected(selected, selected_ids, candidates, limit):
    if len(selected) >= limit:
        return
    for candidate in candidates:
        if id(candidate) in selected_ids:
            continue
        selected.append(candidate)
        selected_ids.add(id(candidate))
        if len(selected) >= limit:
            break


def _select_face_alternatives(candidates, count, maximum_boundary_overlap):
    selected = []
    selected_boundaries = []
    selected_ids = set()
    feasible = [candidate for candidate in candidates if candidate["feasible"]]
    for candidate in feasible:
        boundary = frozenset(candidate["boundary_segment_ids"])
        if any(
            len(boundary & other) / len(boundary | other)
            > maximum_boundary_overlap
            for other in selected_boundaries
        ):
            continue
        selected.append(candidate)
        selected_boundaries.append(boundary)
        selected_ids.add(id(candidate))
        if len(selected) >= count:
            break
    _append_unselected(selected, selected_ids, feasible, count)
    _append_unselected(selected, selected_ids, candidates, count)
    return selected[:count]


def generate_face_region_loops(index, topology, request, settings=None):
    """Grow deterministic randomized face regions and rank usable boundaries."""
    started = time.perf_counter()
    settings = dict(settings or {})
    embedding, blocked_segments, profile_summary = _prepare_face_embedding(
        index, topology, settings
    )
    face_by_id = {item["id"]: item for item in embedding["faces"]}
    adjacent = _face_adjacency(embedding)
    start = index.document["start_snap"]["id"]
    distances = shortest_distances(index, start, blocked_segments=blocked_segments)
    search_settings = _parse_face_search_settings(request, settings)
    seeds = _reachable_face_seeds(
        face_by_id, distances, search_settings.seed_count
    )
    search_data = _FaceSearchData(
        index=index,
        face_by_id=face_by_id,
        adjacent=adjacent,
        distances=distances,
        start=start,
        blocked_segments=blocked_segments,
    )
    search_results = _search_face_regions(search_data, search_settings, seeds)
    candidates = search_results.candidates
    selected_candidates = _select_face_alternatives(
        candidates,
        search_settings.alternative_count,
        search_settings.maximum_boundary_overlap,
    )
    for offset, candidate in enumerate(selected_candidates, 1):
        candidate["id"] = f"face-alternative-{offset}"
        del candidate["_rank"]
    return {
        "schema_version": 1,
        "method": "connected-face-region-boundary",
        "start_node_id": start,
        "request": dict(request),
        "settings": settings,
        "effective_edge_profile": embedding.get("profile"),
        "profile_summary": profile_summary,
        "seed_face_ids": [face_id for _, face_id in seeds],
        "attempted_region_count": search_results.attempted_regions,
        "expanded_growth_state_count": search_results.expanded_state_count,
        "proposed_growth_chunk_count": search_results.proposed_chunk_count,
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
                "base_face_count": candidate["base_face_count"],
                "profile_id": (
                    (result.get("effective_edge_profile") or {}).get("id")
                ),
                "region_area_m2": candidate["region_area_m2"],
                "shape_score": candidate["shape_score"],
                **candidate["shape_metrics"],
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

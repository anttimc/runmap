"""Precompute reusable profile-compatible running cycles."""

from collections import defaultdict
import json
import math
from pathlib import Path
import random
import time

from runmap.routing.face_search import (
    _arc_ids,
    _face_adjacency,
    _growth_chunks,
    _growth_state,
    _prepare_face_embedding,
    _reverse_order,
    _shape_score,
)
from runmap.routing.search import route_coordinates, route_metrics
from runmap.routing.search import shortest_distances


def _centroid(face):
    if "centroid" in face:
        return tuple(face["centroid"])
    coordinates = face["geometry"][:-1] or face["geometry"]
    return (
        sum(point[0] for point in coordinates) / len(coordinates),
        sum(point[1] for point in coordinates) / len(coordinates),
    )


def _centroid_distance_m(first, second):
    mean_latitude = (first[1] + second[1]) / 2
    east = (first[0] - second[0]) * 111000 * math.cos(math.radians(mean_latitude))
    north = (first[1] - second[1]) * 111000
    return math.hypot(east, north)


def _face_access(face, outward, returning, budget):
    portals = [
        {
            "node_id": node_id,
            "outward_distance_m": round(outward[node_id], 3),
            "return_distance_m": round(returning[node_id], 3),
        }
        for node_id in face["node_ids"]
        if outward.get(node_id, math.inf) <= budget
        and returning.get(node_id, math.inf) <= budget
    ]
    portals.sort(key=lambda item: (
        max(item["outward_distance_m"], item["return_distance_m"]),
        item["outward_distance_m"] + item["return_distance_m"],
        item["node_id"],
    ))
    return portals


def _access_ordered_seeds(
    faces, count, outward, returning, budget, minimum_spacing_m
):
    """Select reachable seeds by access cost with deterministic spatial spread."""
    if count < 1 or not faces:
        return [], {}
    points = {face_id: _centroid(face) for face_id, face in faces.items()}
    access = {
        face_id: _face_access(face, outward, returning, budget)
        for face_id, face in faces.items()
    }
    ordered = sorted(
        (face_id for face_id in faces if access[face_id]),
        key=lambda face_id: (
            max(
                access[face_id][0]["outward_distance_m"],
                access[face_id][0]["return_distance_m"],
            ),
            access[face_id][0]["outward_distance_m"]
            + access[face_id][0]["return_distance_m"],
            face_id,
        ),
    )
    selected = []
    deferred = []
    for face_id in ordered:
        if all(
            _centroid_distance_m(points[face_id], points[chosen])
            >= minimum_spacing_m
            for chosen in selected
        ):
            selected.append(face_id)
        else:
            deferred.append(face_id)
        if len(selected) >= count:
            break
    if len(selected) < count:
        selected.extend(deferred[:count - len(selected)])
    return selected, access


def _face_component_sizes(faces, adjacent):
    sizes = {}
    unseen = set(faces)
    while unseen:
        first = min(unseen)
        component = {first}
        frontier = [first]
        while frontier:
            face_id = frontier.pop()
            additions = set(adjacent.get(face_id, ())) - component
            component.update(additions)
            frontier.extend(additions)
        for face_id in component:
            sizes[face_id] = len(component)
        unseen.difference_update(component)
    return sizes


def _boundary_with_chunk(index, boundary, boundary_length_m, faces, chunk):
    result = set(boundary)
    length = boundary_length_m
    for face_id in sorted(chunk):
        for segment_id in faces[face_id]["segment_ids"]:
            if segment_id in result:
                result.remove(segment_id)
                length -= float(index.segments[segment_id]["length_m"])
            else:
                result.add(segment_id)
                length += float(index.segments[segment_id]["length_m"])
    return result, length


def _cycle_from_state(
    index, faces, state, target, seed_id, variant, settings, outward, returning
):
    cycle_options = []
    for direction in (state["ordered"], _reverse_order(state["ordered"])):
        arc_ids = _arc_ids(index, direction)
        if arc_ids is not None:
            metrics = route_metrics(index, arc_ids)
            cycle_options.append({
                "boundary_arc_ids": list(arc_ids),
                "metrics": metrics,
            })
    if not cycle_options:
        return None
    cycle_options.sort(key=lambda option: (
        option["metrics"]["ascent_m"], option["boundary_arc_ids"]
    ))
    primary = cycle_options[0]
    shape = state["shape_metrics"]
    maximum_sharp = int(settings["turns"].get("maximum_sharp_turns", 0))
    if shape["sharp_turn_count"] > maximum_sharp:
        return None
    base_face_ids = sorted({
        base_face_id
        for face_id in state["selected"]
        for base_face_id in faces[face_id].get("member_face_ids", [face_id])
    })
    node_ids = [item[1] for item in state["ordered"]]
    portals = _face_access(
        {"node_ids": sorted(set(node_ids))},
        outward,
        returning,
        settings["access_budget_m"],
    )[:settings["maximum_portals"]]
    if not portals:
        return None
    coordinates = [
        [index.nodes[node_id]["longitude"], index.nodes[node_id]["latitude"]]
        for node_id in node_ids
    ]
    distance_km = max(primary["metrics"]["distance_m"] / 1000, 0.1)
    return {
        "target_band_m": target,
        "source": {
            "kind": "face-region",
            "seed_face_id": seed_id,
            "variant": variant,
            "chunk_sizes": state["chunk_sizes"],
        },
        "face_ids": sorted(state["selected"]),
        "base_face_ids": base_face_ids,
        "face_count": len(state["selected"]),
        "base_face_count": len(base_face_ids),
        "boundary_segment_ids": sorted(state["boundary"]),
        "boundary_arc_ids": primary["boundary_arc_ids"],
        "orientations": cycle_options,
        "portal_node_ids": [portal["node_id"] for portal in portals],
        "portals": portals,
        "region_area_m2": round(state["area_m2"], 3),
        "centroid": [
            sum(point[0] for point in coordinates) / len(coordinates),
            sum(point[1] for point in coordinates) / len(coordinates),
        ],
        "bounds": [
            min(point[0] for point in coordinates),
            min(point[1] for point in coordinates),
            max(point[0] for point in coordinates),
            max(point[1] for point in coordinates),
        ],
        "metrics": primary["metrics"],
        "shape_metrics": shape,
        "shape_score": round(_shape_score(shape, settings["turns"]), 6),
        "quality_score_per_km": round(
            _shape_score(shape, settings["turns"]) / distance_km, 6
        ),
    }


def _multiband_growth_rank(state, targets, tolerances, settings, jitter):
    perimeter = state["boundary_length_m"]
    distance_to_band = min(
        max(abs(perimeter - target) - tolerances[target], 0)
        for target in targets
    )
    shape = state["shape_metrics"]
    return (
        distance_to_band
        + settings["shape_weight"] * _shape_score(shape, settings["turns"])
        + shape["sharp_turn_count"] * settings["near_reversal_weight"]
        + jitter,
        len(state["selected"]),
        tuple(sorted(state["selected"])),
    )


def _search_seed_multiband(
    index, faces, adjacent, seed_id, targets, tolerances, settings, variant,
    outward, returning,
):
    """Grow one deterministic state beam and emit into every length band."""
    rng = random.Random(
        settings["seed_offsets"][seed_id] * 1009 + variant * 9173 + 41
    )
    initial_target = min(targets, key=lambda target: abs(
        sum(float(index.segments[segment_id]["length_m"])
            for segment_id in faces[seed_id]["segment_ids"]) - target
    ))
    state = _growth_state(
        index,
        faces,
        {seed_id},
        faces[seed_id]["segment_ids"],
        initial_target,
        settings["turns"],
        settings["sharp_threshold"],
        settings["shape_weight"],
        settings["near_reversal_weight"],
        0,
    )
    if state is None:
        return defaultdict(list), 0, 0, 0, 0
    state["chunk_sizes"] = []
    states = [state]
    visited = {state["selected"]}
    candidates = defaultdict(list)
    expanded_states = 0
    proposed_chunks = 0
    pruned_states = 0
    duplicate_states = 0
    for _ in range(settings["maximum_faces"]):
        for current in states:
            for target in targets:
                if (
                    len(candidates[target]) >= settings["candidates_per_variant"]
                    or abs(current["boundary_length_m"] - target)
                    > tolerances[target]
                ):
                    continue
                candidate = _cycle_from_state(
                    index, faces, current, target, seed_id, variant, settings,
                    outward, returning,
                )
                if candidate is not None:
                    candidates[target].append(candidate)
        if all(
            len(candidates[target]) >= settings["candidates_per_variant"]
            for target in targets
        ):
            break
        proposals = []
        proposed_signatures = set()
        for current in states:
            expanded_states += 1
            chunks = _growth_chunks(
                current["selected"],
                adjacent,
                settings["maximum_chunk_size"],
                settings["chunk_proposals_per_size"],
                rng,
            )
            proposed_chunks += len(chunks)
            for chunk in chunks:
                selected = current["selected"] | chunk
                if (
                    len(selected) > settings["maximum_faces"]
                    or selected in visited
                    or selected in proposed_signatures
                ):
                    if selected in visited or selected in proposed_signatures:
                        duplicate_states += 1
                    continue
                proposed_signatures.add(selected)
                boundary, boundary_length_m = _boundary_with_chunk(
                    index,
                    current["boundary"],
                    current["boundary_length_m"],
                    faces,
                    chunk,
                )
                area_m2 = current["area_m2"] + sum(
                    float(faces[face_id]["area_m2"]) for face_id in chunk
                )
                nearest_target = min(
                    targets,
                    key=lambda target: abs(boundary_length_m - target),
                )
                jitter = rng.random() * settings["jitter"] * (variant + 1)
                proposed = _growth_state(
                    index,
                    faces,
                    selected,
                    boundary,
                    nearest_target,
                    settings["turns"],
                    settings["sharp_threshold"],
                    settings["shape_weight"],
                    settings["near_reversal_weight"],
                    0,
                    area_m2=area_m2,
                    boundary_length_m=boundary_length_m,
                )
                if proposed is not None:
                    proposed["chunk_sizes"] = current["chunk_sizes"] + [len(chunk)]
                    proposed["multiband_rank"] = _multiband_growth_rank(
                        proposed, targets, tolerances, settings, jitter
                    )
                    proposals.append(proposed)
        states = []
        retained_per_bucket = defaultdict(int)
        ordered_proposals = sorted(
            proposals, key=lambda item: item["multiband_rank"]
        )
        for offset, proposed in enumerate(ordered_proposals):
            if proposed["selected"] in visited:
                continue
            visited.add(proposed["selected"])
            bucket = int(
                proposed["boundary_length_m"] // settings["perimeter_bucket_m"]
            )
            if retained_per_bucket[bucket] >= settings["beam_width"]:
                pruned_states += 1
                continue
            retained_per_bucket[bucket] += 1
            states.append(proposed)
            if len(states) >= settings["maximum_states_per_round"]:
                pruned_states += len(ordered_proposals) - offset - 1
                break
        if not states:
            break
    return (
        candidates,
        expanded_states,
        proposed_chunks,
        pruned_states,
        duplicate_states,
    )


def _boundary_overlap(first, second, segment_lengths):
    intersection = first & second
    union = first | second
    return (
        sum(segment_lengths[segment_id] for segment_id in intersection)
        / sum(segment_lengths[segment_id] for segment_id in union)
        if union else 0
    )


def _select_diverse(
    candidates, maximum_per_band, maximum_overlap, segment_lengths
):
    selected = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item["quality_score_per_km"],
            item["shape_metrics"]["maximum_turn_degrees"],
            abs(item["metrics"]["distance_m"] - item["target_band_m"]),
        ),
    ):
        boundary = set(candidate["boundary_segment_ids"])
        if any(
            _boundary_overlap(
                boundary,
                set(other["boundary_segment_ids"]),
                segment_lengths,
            ) > maximum_overlap
            for other in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= maximum_per_band:
            break
    return selected


def _shared_boundary_length(first, second, segment_lengths):
    return sum(
        segment_lengths[segment_id]
        for segment_id in set(first) & set(second)
    )


def _module_components(modules, minimum_shared_m, segment_lengths):
    """Find groups joined by a shared boundary and disjoint face interiors."""
    by_id = {module["candidate_id"]: module for module in modules}
    adjacent = {module_id: set() for module_id in by_id}
    module_ids = sorted(by_id)
    for offset, first_id in enumerate(module_ids):
        first = by_id[first_id]
        for second_id in module_ids[offset + 1:]:
            second = by_id[second_id]
            if set(first["face_ids"]) & set(second["face_ids"]):
                continue
            shared_m = _shared_boundary_length(
                first["boundary_segment_ids"],
                second["boundary_segment_ids"],
                segment_lengths,
            )
            if shared_m >= minimum_shared_m:
                adjacent[first_id].add(second_id)
                adjacent[second_id].add(first_id)
    components = []
    unseen = {module_id for module_id in by_id if adjacent[module_id]}
    while unseen:
        first_id = min(unseen)
        component = {first_id}
        frontier = [first_id]
        unseen.remove(first_id)
        while frontier:
            module_id = frontier.pop()
            additions = adjacent[module_id] & unseen
            unseen.difference_update(additions)
            component.update(additions)
            frontier.extend(sorted(additions))
        components.append(component)
    return adjacent, components


def _composition_rank(cycle, nominal_target_m):
    return (
        abs(cycle["metrics"]["distance_m"] - nominal_target_m),
        cycle["shape_score"],
        -cycle["region_area_m2"],
        tuple(cycle["face_ids"]),
    )


def _compose_cycle_modules(
    index,
    faces,
    candidate_pool,
    composition_settings,
    search_settings,
    outward,
    returning,
    segment_lengths,
):
    """Build simple envelope compositions from several accessible root cycles."""
    if not composition_settings:
        return None
    source_band_m = float(composition_settings.get("source_band_m", 1000))
    root_count = int(composition_settings.get("root_count", 3))
    maximum_modules = int(composition_settings.get("maximum_modules", 4))
    beam_width = int(composition_settings.get("beam_width_per_root", 6))
    minimum_shared_m = float(composition_settings.get(
        "minimum_shared_boundary_m", 5
    ))
    if (
        source_band_m <= 0
        or root_count < 1
        or maximum_modules < 2
        or beam_width < 1
        or minimum_shared_m <= 0
    ):
        raise ValueError("cycle composition settings must be positive")
    modules = [
        candidate
        for candidate in candidate_pool
        if float(candidate["target_band_m"]) == source_band_m
    ]
    by_id = {module["candidate_id"]: module for module in modules}
    adjacent, components = _module_components(
        modules, minimum_shared_m, segment_lengths
    )

    def access_rank(module_id):
        module = by_id[module_id]
        portal = module["portals"][0]
        return (
            max(portal["outward_distance_m"], portal["return_distance_m"]),
            module["quality_score_per_km"],
            -len(adjacent[module_id]),
            module_id,
        )

    roots = [min(component, key=access_rank) for component in components]
    roots.sort(key=access_rank)
    roots = roots[:root_count]
    compositions = []
    active_by_root = {}
    seen_by_root = {}
    for root_id in roots:
        root = by_id[root_id]
        active_by_root[root_id] = [{
            "id": root_id,
            "module_ids": (root_id,),
            "face_ids": frozenset(root["face_ids"]),
            "boundary": frozenset(root["boundary_segment_ids"]),
            "area_m2": float(root["region_area_m2"]),
            "metrics": root["metrics"],
            "removed_shared_boundary_m": 0.0,
        }]
        seen_by_root[root_id] = {frozenset(root["face_ids"])}

    next_id = 1
    rejected = defaultdict(int)
    level_summary = []
    for module_count in range(2, maximum_modules + 1):
        next_by_root = {}
        generated_at_level = 0
        for root_id in roots:
            proposals_by_faces = {}
            for current in active_by_root.get(root_id, ()):
                for module in modules:
                    module_id = module["candidate_id"]
                    module_faces = frozenset(module["face_ids"])
                    if module_id in current["module_ids"]:
                        continue
                    if current["face_ids"] & module_faces:
                        rejected["interior_overlap"] += 1
                        continue
                    shared_m = _shared_boundary_length(
                        current["boundary"],
                        module["boundary_segment_ids"],
                        segment_lengths,
                    )
                    if shared_m < minimum_shared_m:
                        continue
                    selected = current["face_ids"] | module_faces
                    if selected in seen_by_root[root_id]:
                        rejected["duplicate_region"] += 1
                        continue
                    boundary = (
                        set(current["boundary"])
                        ^ set(module["boundary_segment_ids"])
                    )
                    boundary_length_m = (
                        current["metrics"]["distance_m"]
                        + module["metrics"]["distance_m"]
                        - 2 * shared_m
                    )
                    area_m2 = current["area_m2"] + float(module["region_area_m2"])
                    nominal_target_m = module_count * source_band_m
                    state = _growth_state(
                        index,
                        faces,
                        selected,
                        boundary,
                        nominal_target_m,
                        search_settings["turns"],
                        search_settings["sharp_threshold"],
                        search_settings["shape_weight"],
                        search_settings["near_reversal_weight"],
                        0,
                        area_m2=area_m2,
                        boundary_length_m=boundary_length_m,
                    )
                    if state is None:
                        rejected["non_simple_boundary"] += 1
                        continue
                    state["chunk_sizes"] = []
                    cycle = _cycle_from_state(
                        index,
                        faces,
                        state,
                        nominal_target_m,
                        root_id,
                        0,
                        search_settings,
                        outward,
                        returning,
                    )
                    if cycle is None:
                        rejected["route_validation"] += 1
                        continue
                    cycle["source"] = {
                        "kind": "cycle-envelope-composition",
                        "root_candidate_id": root_id,
                        "parent_id": current["id"],
                        "added_candidate_id": module_id,
                        "module_candidate_ids": list(current["module_ids"])
                        + [module_id],
                    }
                    cycle["module_count"] = module_count
                    cycle["shared_boundary_m"] = round(shared_m, 3)
                    cycle["removed_shared_boundary_m"] = round(
                        current["removed_shared_boundary_m"] + shared_m, 3
                    )
                    cycle["marginal_length_m"] = round(
                        cycle["metrics"]["distance_m"]
                        - current["metrics"]["distance_m"],
                        3,
                    )
                    signature = frozenset(cycle["face_ids"])
                    previous = proposals_by_faces.get(signature)
                    if (
                        previous is None
                        or _composition_rank(cycle, nominal_target_m)
                        < _composition_rank(previous, nominal_target_m)
                    ):
                        proposals_by_faces[signature] = cycle
            ordered = sorted(
                proposals_by_faces.values(),
                key=lambda cycle: _composition_rank(
                    cycle, module_count * source_band_m
                ),
            )[:beam_width]
            states = []
            for cycle in ordered:
                cycle["id"] = f"composition-{next_id}"
                next_id += 1
                compositions.append(cycle)
                signature = frozenset(cycle["face_ids"])
                seen_by_root[root_id].add(signature)
                states.append({
                    "id": cycle["id"],
                    "module_ids": tuple(
                        cycle["source"]["module_candidate_ids"]
                    ),
                    "face_ids": signature,
                    "boundary": frozenset(cycle["boundary_segment_ids"]),
                    "area_m2": float(cycle["region_area_m2"]),
                    "metrics": cycle["metrics"],
                    "removed_shared_boundary_m": cycle[
                        "removed_shared_boundary_m"
                    ],
                })
            next_by_root[root_id] = states
            generated_at_level += len(states)
        active_by_root = next_by_root
        level_summary.append({
            "module_count": module_count,
            "composition_count": generated_at_level,
        })
        if not any(active_by_root.values()):
            break
    return {
        "method": "rooted-disjoint-envelope-composition",
        "settings": composition_settings,
        "source_band_m": source_band_m,
        "root_candidate_ids": roots,
        "compatible_module_component_count": len(components),
        "composition_count": len(compositions),
        "level_summary": level_summary,
        "rejected": dict(sorted(rejected.items())),
        "cycles": compositions,
    }


def generate_cycle_catalog(index, topology, face_settings, catalog_settings):
    """Generate reusable cycles without using a final route request."""
    started = time.perf_counter()
    face_settings = dict(face_settings or {})
    catalog_settings = dict(catalog_settings or {})
    embedding, blocked_segments, profile_summary = _prepare_face_embedding(
        index, topology, face_settings
    )
    faces = {face["id"]: face for face in embedding["faces"]}
    adjacent = _face_adjacency(embedding)
    component_sizes = _face_component_sizes(faces, adjacent)
    minimum_component_faces = int(
        catalog_settings.get("minimum_seed_component_faces", 1)
    )
    seedable_faces = {
        face_id: face
        for face_id, face in faces.items()
        if component_sizes[face_id] >= minimum_component_faces
    }
    targets = [float(value) for value in catalog_settings.get(
        "target_cycle_lengths_m", [1000, 2000, 3000, 4000]
    )]
    growth = face_settings.get("growth", {})
    turns = face_settings.get("turns", {})
    settings = {
        "turns": turns,
        "sharp_threshold": float(turns.get("sharp_threshold_degrees", 170)),
        "beam_width": int(catalog_settings.get("beam_width", growth.get("beam_width", 3))),
        "maximum_chunk_size": int(growth.get("maximum_chunk_size", 3)),
        "chunk_proposals_per_size": int(catalog_settings.get(
            "chunk_proposals_per_size", growth.get("chunk_proposals_per_size", 12)
        )),
        "shape_weight": float(growth.get("shape_weight", 1)),
        "near_reversal_weight": float(growth.get("near_reversal_weight", 750)),
        "jitter": float(growth.get("jitter", 75)),
        "maximum_faces": int(catalog_settings.get("maximum_faces", 150)),
        "candidates_per_variant": int(catalog_settings.get("candidates_per_variant", 2)),
        "access_budget_m": float(catalog_settings.get(
            "one_way_access_budget_m", math.inf
        )),
        "maximum_portals": int(catalog_settings.get("maximum_portals_per_cycle", 8)),
        "perimeter_bucket_m": float(catalog_settings.get(
            "coarse_perimeter_bucket_m", 500
        )),
    }
    if not targets or any(target <= 0 for target in targets):
        raise ValueError("cycle catalog target lengths must be positive")
    if (
        settings["access_budget_m"] <= 0
        or settings["maximum_portals"] < 1
        or settings["perimeter_bucket_m"] <= 0
    ):
        raise ValueError("cycle catalog access budget and portal count must be positive")
    start = index.document["start_snap"]["id"]
    outward = shortest_distances(
        index,
        start,
        maximum=settings["access_budget_m"],
        blocked_segments=blocked_segments,
    )
    returning = shortest_distances(
        index,
        start,
        maximum=settings["access_budget_m"],
        blocked_segments=blocked_segments,
        reverse=True,
    )
    minimum_seed_spacing_m = float(catalog_settings.get(
        "minimum_seed_spacing_m", 0
    ))
    if minimum_seed_spacing_m < 0:
        raise ValueError("cycle catalog seed spacing must be non-negative")
    seed_ids, face_access = _access_ordered_seeds(
        seedable_faces,
        int(catalog_settings.get(
            "seed_count", catalog_settings.get("spatial_seed_count", 12)
        )),
        outward,
        returning,
        settings["access_budget_m"],
        minimum_seed_spacing_m,
    )
    settings["seed_offsets"] = {
        face_id: offset for offset, face_id in enumerate(seed_ids)
    }
    variants = int(catalog_settings.get("variants_per_seed", 2))
    tolerance_fraction = float(catalog_settings.get("length_tolerance_fraction", 0.12))
    tolerances = {target: target * tolerance_fraction for target in targets}
    default_state_limit = settings["beam_width"] * (
        math.ceil(max(targets) / settings["perimeter_bucket_m"]) + 2
    )
    settings["maximum_states_per_round"] = int(catalog_settings.get(
        "maximum_states_per_round", default_state_limit
    ))
    if settings["maximum_states_per_round"] < 1:
        raise ValueError("cycle catalog state limit must be positive")
    maximum_per_band = int(catalog_settings.get("maximum_cycles_per_band", 12))
    maximum_overlap = float(catalog_settings.get("maximum_cycle_overlap", 0.75))
    raw_by_band = defaultdict(list)
    seen_boundaries = set()
    expanded_states = 0
    proposed_chunks = 0
    pruned_states = 0
    duplicate_states = 0
    for seed_id in seed_ids:
        for variant in range(variants):
            found, expanded, proposed, pruned, duplicate = _search_seed_multiband(
                index, faces, adjacent, seed_id, targets, tolerances, settings,
                variant, outward, returning,
            )
            expanded_states += expanded
            proposed_chunks += proposed
            pruned_states += pruned
            duplicate_states += duplicate
            for target, candidates in found.items():
                for candidate in candidates:
                    identity = frozenset(candidate["boundary_segment_ids"])
                    if identity not in seen_boundaries:
                        seen_boundaries.add(identity)
                        raw_by_band[target].append(candidate)
    segment_lengths = {
        segment_id: float(segment["length_m"])
        for segment_id, segment in index.segments.items()
    }
    candidate_pool = []
    for target in targets:
        candidate_pool.extend(sorted(
            raw_by_band[target],
            key=lambda item: (
                item["quality_score_per_km"],
                tuple(item["boundary_segment_ids"]),
            ),
        ))
    for offset, candidate in enumerate(candidate_pool, 1):
        candidate["candidate_id"] = f"candidate-{offset}"
    composition = _compose_cycle_modules(
        index,
        faces,
        candidate_pool,
        catalog_settings.get("composition"),
        settings,
        outward,
        returning,
        segment_lengths,
    )
    selected = []
    band_summary = []
    for target in targets:
        band_cycles = _select_diverse(
            raw_by_band[target], maximum_per_band, maximum_overlap,
            segment_lengths,
        )
        band_summary.append({
            "target_cycle_length_m": target,
            "generated_cycle_count": len(raw_by_band[target]),
            "selected_cycle_count": len(band_cycles),
        })
        selected.extend(band_cycles)
    selected.sort(key=lambda item: (item["target_band_m"], item["quality_score_per_km"]))
    for offset, cycle in enumerate(selected, 1):
        cycle["id"] = f"cycle-{offset}"
    generated_by_seed = defaultdict(int)
    for candidates in raw_by_band.values():
        for candidate in candidates:
            generated_by_seed[candidate["source"]["seed_face_id"]] += 1
    return {
        "schema_version": 2,
        "method": "start-local-profiled-multiscale-face-cycle-catalog",
        "settings": catalog_settings,
        "effective_edge_profile": embedding.get("profile"),
        "profile_summary": profile_summary,
        "seed_face_ids": seed_ids,
        "seeds": [
            {
                "face_id": face_id,
                "centroid": list(_centroid(faces[face_id])),
                "component_face_count": component_sizes[face_id],
                "outward_distance_m": face_access[face_id][0]["outward_distance_m"],
                "return_distance_m": face_access[face_id][0]["return_distance_m"],
                "portal_node_id": face_access[face_id][0]["node_id"],
                "generated_cycle_count": generated_by_seed[face_id],
            }
            for face_id in seed_ids
        ],
        "band_summary": band_summary,
        "generated_cycle_count": sum(len(items) for items in raw_by_band.values()),
        "selected_cycle_count": len(selected),
        "expanded_growth_state_count": expanded_states,
        "proposed_growth_chunk_count": proposed_chunks,
        "pruned_growth_state_count": pruned_states,
        "duplicate_growth_state_count": duplicate_states,
        "multiband_traversal_count": len(seed_ids) * variants,
        "runtime_seconds": round(time.perf_counter() - started, 3),
        "candidate_pool": candidate_pool,
        "selected_cycle_ids": [cycle["id"] for cycle in selected],
        "cycles": selected,
        "composition": composition,
    }


def cycle_catalog_geojson(index, catalog):
    features = []
    for cycle in catalog["cycles"]:
        features.append({
            "type": "Feature",
            "id": cycle["id"],
            "geometry": {
                "type": "LineString",
                "coordinates": route_coordinates(index, cycle["boundary_arc_ids"]),
            },
            "properties": {
                "id": cycle["id"],
                "target_band_m": cycle["target_band_m"],
                "face_count": cycle["face_count"],
                "base_face_count": cycle["base_face_count"],
                "quality_score_per_km": cycle["quality_score_per_km"],
                **cycle["shape_metrics"],
                **cycle["metrics"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def cycle_composition_geojson(index, catalog):
    composition = catalog.get("composition") or {"cycles": []}
    features = []
    for cycle in composition["cycles"]:
        features.append({
            "type": "Feature",
            "id": cycle["id"],
            "geometry": {
                "type": "LineString",
                "coordinates": route_coordinates(index, cycle["boundary_arc_ids"]),
            },
            "properties": {
                "id": cycle["id"],
                "root_candidate_id": cycle["source"]["root_candidate_id"],
                "parent_id": cycle["source"]["parent_id"],
                "added_candidate_id": cycle["source"]["added_candidate_id"],
                "module_count": cycle["module_count"],
                "shared_boundary_m": cycle["shared_boundary_m"],
                "removed_shared_boundary_m": cycle[
                    "removed_shared_boundary_m"
                ],
                "marginal_length_m": cycle["marginal_length_m"],
                **cycle["shape_metrics"],
                **cycle["metrics"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def write_cycle_catalog_outputs(index, catalog, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        "catalog": (directory / "cycle-catalog.json", catalog),
        "geojson": (
            directory / "cycle-catalog.geojson",
            cycle_catalog_geojson(index, catalog),
        ),
    }
    if catalog.get("composition") is not None:
        outputs["compositions"] = (
            directory / "cycle-compositions.geojson",
            cycle_composition_geojson(index, catalog),
        )
    for path, document in outputs.values():
        partial = path.with_suffix(path.suffix + ".part")
        partial.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
        partial.replace(path)
    return {name: path for name, (path, _) in outputs.items()}

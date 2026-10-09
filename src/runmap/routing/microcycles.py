"""Deterministic local repair of a disjoint atomic-region partition."""

from collections import Counter, defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path
import time

from runmap.routing.atomic_regions import STATUSES, atomic_regions_geojson, evaluate_region_boundary
from runmap.routing.profiles import classify_segments
from runmap.routing.search import shortest_distances


RANK_FIELDS = (
    "not_accepted", "standalone_deficit", "convexity_deficit", "accepted_neighbor_consumed",
    "negative_shape_repair_gain", "atomic_region_count", "area_m2", "negative_convexity",
    "perimeter_m", "negative_isoperimetric_quotient", "turn_penalty", "neighbor_id",
)


def _interfaces(index, active, forbidden):
    owners = defaultdict(list)
    for region_id, region in sorted(active.items()):
        if region["status"] == "invalid":
            continue
        for segment in region["boundary_segment_ids"]:
            owners[segment].append(region_id)
    adjacent = {region_id: {} for region_id in active}
    for segment, members in sorted(owners.items()):
        if len(members) > 2:
            raise ValueError(f"Non-manifold microcycle interface: {segment}")
        if len(members) != 2 or segment in forbidden or segment in index.boundary_blocked_segments:
            continue
        first, second = members
        adjacent[first].setdefault(second, []).append(segment)
        adjacent[second].setdefault(first, []).append(segment)
    return adjacent


def _deficit(region, floor):
    return (int(bool(region["standalone_rejection_reasons"])),
            max(0, floor - region["shape_metrics"]["convexity_ratio"]))


def _reachability(index, region, outward, returning):
    nodes = sorted({node for segment in region["boundary_segment_ids"] for node in (
        index.segments[segment]["from"], index.segments[segment]["to"]
    )})
    portals = [{"node_id": node, "outward_distance_m": round(outward[node], 3),
                "return_distance_m": round(returning[node], 3)}
               for node in nodes if node in outward and node in returning]
    portals.sort(key=lambda portal: (portal["outward_distance_m"] + portal["return_distance_m"], portal["node_id"]))
    return {"outward_reachable": any(node in outward for node in nodes),
            "return_reachable": any(node in returning for node in nodes),
            "round_trip_reachable": bool(portals), "portals": portals}


def verify_microcycle_partition(index, result):
    """Verify active ownership, the binary tree, interfaces, and exact unions."""
    nodes = {node["id"]: node for node in result["nodes"]}
    if len(nodes) != len(result["nodes"]):
        raise ValueError("Duplicate microcycle node IDs")
    active_ids = result["active_region_ids"]
    if len(active_ids) != len(set(active_ids)):
        raise ValueError("Duplicate active microcycles")
    atomic_owner, face_owner = {}, {}
    for node in nodes.values():
        if node["active"] != (node["id"] in active_ids):
            raise ValueError("Microcycle active flags disagree with the partition")
        children = node["children"]
        if node["parent"] is not None:
            if node["parent"] not in nodes or node["id"] not in nodes[node["parent"]]["children"]:
                raise ValueError("Microcycle parent/child links disagree")
        if not children:
            continue
        if len(children) != 2 or any(child not in nodes for child in children):
            raise ValueError("Microcycle repairs must form a binary tree")
        first, second = (nodes[child] for child in children)
        if any(child["parent"] != node["id"] for child in (first, second)):
            raise ValueError("Microcycle parent/child links disagree")
        if node["active"] and any(child["active"] for child in (first, second)):
            raise ValueError("A merge parent and its children cannot be active together")
        if set(first["atomic_region_ids"]) & set(second["atomic_region_ids"]):
            raise ValueError("Merge children have overlapping atomic membership")
        if set(first["member_face_ids"]) & set(second["member_face_ids"]):
            raise ValueError("Merge children have overlapping base faces")
        for key in ("atomic_region_ids", "member_face_ids"):
            if sorted(node[key]) != sorted(first[key] + second[key]):
                raise ValueError(f"Merge does not reproduce {key}")
        shared = set(first["boundary_segment_ids"]) & set(second["boundary_segment_ids"])
        if not shared or sorted(shared) != node["merge_interface"]["segment_ids"]:
            raise ValueError("Merge interface disagrees with child boundaries")
        boundary = set(first["boundary_segment_ids"]) ^ set(second["boundary_segment_ids"])
        if boundary != set(node["boundary_segment_ids"]):
            raise ValueError("Merge boundary is not the child symmetric difference")
        shared_length = sum(float(index.segments[segment]["length_m"]) for segment in shared)
        expected = first["perimeter_m"] + second["perimeter_m"] - 2 * shared_length
        if not math.isclose(node["perimeter_m"], expected, abs_tol=0.005, rel_tol=0):
            raise ValueError("Merge perimeter identity failed")
        if not math.isclose(node["area_m2"], first["area_m2"] + second["area_m2"], abs_tol=0.002, rel_tol=0):
            raise ValueError("Merge area identity failed")
        if node["level"] != max(first["level"], second["level"]) + 1:
            raise ValueError("Merge levels do not form an acyclic tree")
    for node_id in active_ids:
        node = nodes[node_id]
        for items, owner in ((node["atomic_region_ids"], atomic_owner), (node["member_face_ids"], face_owner)):
            for member in items:
                if member in owner:
                    raise ValueError("Active microcycles have overlapping membership")
                owner[member] = node_id
        if node["status"] != "invalid" and not node["legal_orientations"]:
            raise ValueError("Usable microcycle has no legal orientation")
        if node["status"] == "accepted" and (node["quality_reasons"] or node["standalone_rejection_reasons"]):
            raise ValueError("Accepted microcycle fails standalone policy")
    if dict(sorted(atomic_owner.items())) != result["atomic_region_to_microcycle"]:
        raise ValueError("Atomic ownership disagrees with the microcycle partition")
    if dict(sorted(face_owner.items())) != result["base_face_to_microcycle"]:
        raise ValueError("Base-face ownership disagrees with the microcycle partition")
    if len(atomic_owner) != result["summary"]["atomic_region_count"] or len(face_owner) != result["summary"]["base_face_count"]:
        raise ValueError("Microcycle partition lost source membership")
    if result["regions"] != [nodes[node_id] for node_id in active_ids]:
        raise ValueError("Active microcycle records disagree with the hierarchy")
    expected_adjacency = _interfaces(index, {node_id: nodes[node_id] for node_id in active_ids},
                                    classify_segments(index, result["effective_edge_profile"]))
    expected = {(first, second): segments for first, neighbors in expected_adjacency.items()
                for second, segments in neighbors.items() if first < second}
    actual = {tuple(interface["region_ids"]): interface["segment_ids"] for interface in result["shared_interfaces"]}
    if len(actual) != len(result["shared_interfaces"]) or actual != expected:
        raise ValueError("Microcycle shared interfaces disagree with boundary incidences")
    for interface in result["shared_interfaces"]:
        first, second = interface["region_ids"]
        for member, neighbor in ((first, second), (second, first)):
            matches = [entry for entry in nodes[member]["neighbors"] if entry["region_id"] == neighbor]
            if len(matches) != 1 or matches[0] != {
                "region_id": neighbor, "interface_id": interface["id"],
                "segment_ids": interface["segment_ids"], "length_m": interface["length_m"],
            }:
                raise ValueError("Microcycle interfaces are not symmetric")
        length = sum(float(index.segments[segment]["length_m"]) for segment in interface["segment_ids"])
        if not math.isclose(interface["length_m"], length, abs_tol=0.001, rel_tol=0):
            raise ValueError("Microcycle interface length disagrees with its segments")
    return True


def build_microcycle_partition(index, inventory, settings=None):
    """Repair only policy-poor regions, without a requested route length."""
    started = time.perf_counter()
    settings = dict(settings or {})
    maximum_atoms = settings.get("maximum_atomic_regions_per_microcycle", 4)
    maximum_merges = settings.get("maximum_merges", max(0, len(inventory["regions"]) - 1))
    for name, value, minimum in (("maximum_atomic_regions_per_microcycle", maximum_atoms, 1),
                                 ("maximum_merges", maximum_merges, 0)):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} must be an integer at least {minimum}")
    minimum_gain = float(settings.get("minimum_convexity_improvement", 0.000001))
    if not math.isfinite(minimum_gain) or not 0 < minimum_gain <= 1:
        raise ValueError("minimum_convexity_improvement must be between zero and one")
    profile = inventory["effective_edge_profile"]
    boundary_settings = inventory["settings"]
    floor = boundary_settings["minimum_convexity_ratio"]
    forbidden = classify_segments(index, profile)
    blocked = set(forbidden) | index.boundary_blocked_segments
    access_started = time.perf_counter()
    outward = shortest_distances(index, inventory["start_node_id"], blocked_segments=blocked)
    returning = shortest_distances(index, inventory["start_node_id"], blocked_segments=blocked, reverse=True)
    access_time = time.perf_counter() - access_started
    traversals = defaultdict(list)
    for arc_id, arc in sorted(index.arcs.items()):
        traversals[arc["segment_id"], arc["from"], arc["to"]].append(arc_id)
    nodes, atomic_members, base_members = {}, set(), set()
    for source in sorted(inventory["regions"], key=lambda item: item["id"]):
        node = copy.deepcopy(source)
        if node["id"] in nodes or len(node["member_face_ids"]) != len(set(node["member_face_ids"])):
            raise ValueError("Atomic inventory contains duplicate regions or members")
        if node["atomic_region_ids"] != [node["id"]] or node["children"] or node["parent"] is not None:
            raise ValueError("Microcycle input must contain atomic leaves")
        if base_members & set(node["member_face_ids"]):
            raise ValueError("Atomic input has overlapping base faces")
        if any(inventory["base_face_to_atomic_region"].get(member) != node["id"] for member in node["member_face_ids"]):
            raise ValueError("Atomic input ownership map disagrees with its members")
        base_members.update(node["member_face_ids"])
        atomic_members.add(node["id"])
        node.update(active=True, neighbors=[], source_status=source["status"], repair_sequence=[],
                    repair_outcome={"reason": "invalid-atomic-region" if source["status"] == "invalid" else "accepted-atomic-region" if source["status"] == "accepted" else "pending-repair"})
        nodes[node["id"]] = node
    if base_members != set(inventory["base_face_to_atomic_region"]):
        raise ValueError("Atomic input does not cover its declared base faces")
    active = dict(nodes)
    adjacent = _interfaces(index, active, forbidden)
    topology_time = time.perf_counter() - started - access_time
    attempts, events = [], []
    validation_time = metric_time = 0.0
    while len(events) < maximum_merges:
        pending = sorted((node for node in active.values() if node["status"] in {"repair-needed", "connector-only"}),
                         key=lambda node: (0 if "low-convexity" in node["quality_reasons"] else 1,
                                           node["shape_metrics"]["convexity_ratio"], tuple(node["atomic_region_ids"])))
        merged_in_round = False
        for target in pending:
            if target["id"] not in active or len(events) >= maximum_merges:
                continue
            candidates = []
            for neighbor_id, interface in sorted(adjacent[target["id"]].items()):
                neighbor = active[neighbor_id]
                shared = set(interface)
                boundary = set(target["boundary_segment_ids"]) ^ set(neighbor["boundary_segment_ids"])
                atom_ids = sorted(target["atomic_region_ids"] + neighbor["atomic_region_ids"])
                attempt = {"target_id": target["id"], "neighbor_id": neighbor_id,
                           "interface_segment_ids": sorted(shared), "selected": False,
                           "rejection_reasons": [], "comparison": None}
                attempts.append(attempt)
                if len(atom_ids) > maximum_atoms:
                    attempt["rejection_reasons"].append("atomic-region-limit")
                    continue
                measurement, timings = evaluate_region_boundary(
                    index, boundary, round(target["area_m2"] + neighbor["area_m2"], 3),
                    profile, boundary_settings, forbidden, traversals,
                )
                validation_time += timings["validation"]
                metric_time += timings["metrics"]
                attempt["candidate_metrics"] = measurement
                if measurement["status"] == "invalid":
                    attempt["rejection_reasons"].extend(measurement["rejection_reasons"])
                    continue
                before, after = _deficit(target, floor), _deficit(measurement, floor)
                if after[0] > before[0] or (after[0] == before[0] and before[1] - after[1] < minimum_gain):
                    attempt["rejection_reasons"].append("no-policy-or-shape-improvement")
                if neighbor["status"] == "accepted" and measurement["status"] != "accepted":
                    attempt["rejection_reasons"].append("accepted-neighbor-regression")
                shape = measurement["shape_metrics"]
                gain = shape["convexity_ratio"] - target["shape_metrics"]["convexity_ratio"]
                rank = (int(measurement["status"] != "accepted"), after[0], round(after[1], 6),
                        int(neighbor["status"] == "accepted"),
                        -gain if "low-convexity" in target["quality_reasons"] else 0,
                        len(atom_ids), measurement["area_m2"], -shape["convexity_ratio"],
                        measurement["perimeter_m"], -shape["isoperimetric_quotient"], shape["turn_penalty"], neighbor_id)
                shared_length = sum(float(index.segments[segment]["length_m"]) for segment in shared)
                attempt["comparison"] = dict(zip(RANK_FIELDS, rank))
                attempt["measurements"] = {
                    "shared_boundary_length_m": round(shared_length, 3),
                    "marginal_length_m": round(measurement["perimeter_m"] - target["perimeter_m"], 3),
                    "new_boundary_length_m": round(sum(float(index.segments[segment]["length_m"])
                                                       for segment in set(neighbor["boundary_segment_ids"]) - shared), 3),
                    "convexity_change": round(gain, 6),
                    "isoperimetric_quotient_change": round(shape["isoperimetric_quotient"] - target["shape_metrics"]["isoperimetric_quotient"], 6),
                    "perimeter_identity_error_m": round(measurement["perimeter_m"] - target["perimeter_m"] - neighbor["perimeter_m"] + 2 * shared_length, 6),
                    "connectivity_preserved": True,
                }
                if not attempt["rejection_reasons"]:
                    candidates.append((rank, neighbor, measurement, attempt))
            if not candidates:
                continue
            candidates.sort(key=lambda candidate: candidate[0])
            _, neighbor, measurement, chosen = candidates[0]
            for _, _, _, attempt in candidates[1:]:
                attempt["rejection_reasons"].append("lower-ranked-neighbor")
            parent_id = f"microcycle-merge-{len(events) + 1}"
            chosen["selected"] = True
            area = measurement["area_m2"]
            parent = {
                **measurement, "id": parent_id, "active": True,
                "level": max(target["level"], neighbor["level"]) + 1,
                "atomic_region_ids": sorted(target["atomic_region_ids"] + neighbor["atomic_region_ids"]),
                "member_face_ids": sorted(target["member_face_ids"] + neighbor["member_face_ids"]),
                "source_profile_region_ids": sorted(target["source_profile_region_ids"] + neighbor["source_profile_region_ids"]),
                "hole_fills": target["hole_fills"] + neighbor["hole_fills"],
                "children": [target["id"], neighbor["id"]], "parent": None, "root_id": None,
                "merge_interface": {"segment_ids": chosen["interface_segment_ids"],
                                    "length_m": chosen["measurements"]["shared_boundary_length_m"]},
                "centroid": [sum(child["centroid"][axis] * child["area_m2"] for child in (target, neighbor)) / area for axis in (0, 1)],
                "neighbors": [], "source_status": None,
                "repair_sequence": target["repair_sequence"] + neighbor["repair_sequence"] + [parent_id],
                "repair_outcome": {"reason": "repaired" if measurement["status"] == "accepted" else "partial-repair"},
            }
            parent["reachability"] = _reachability(index, parent, outward, returning)
            expected_neighbors = (set(adjacent[target["id"]]) | set(adjacent[neighbor["id"]])) - {target["id"], neighbor["id"]}
            for child in (target, neighbor):
                child.update(active=False, parent=parent_id)
                del active[child["id"]]
            nodes[parent_id] = active[parent_id] = parent
            topology_started = time.perf_counter()
            adjacent = _interfaces(index, active, forbidden)
            topology_time += time.perf_counter() - topology_started
            if set(adjacent[parent_id]) != expected_neighbors:
                raise ValueError("Repair merge changed external adjacency connectivity")
            events.append({"id": parent_id, "children": parent["children"],
                           "atomic_region_ids": parent["atomic_region_ids"],
                           "comparison": chosen["comparison"], "measurements": chosen["measurements"],
                           "merge_interface": parent["merge_interface"]})
            merged_in_round = True
        if not merged_in_round:
            break
    for node in active.values():
        if node["status"] not in {"repair-needed", "connector-only"}:
            continue
        reason = ("merge-budget-exhausted" if len(events) >= maximum_merges else
                  "no-runnable-neighbor" if not adjacent[node["id"]] else "no-valid-improving-neighbor")
        node["status"] = "connector-only"
        node["repair_outcome"] = {"reason": reason,
                                  "policy_reasons": node["quality_reasons"] + node["standalone_rejection_reasons"]}
    interfaces = []
    for first in sorted(adjacent):
        for second, segments in sorted(adjacent[first].items()):
            if first >= second:
                continue
            interface = {"id": f"microcycle-interface-{len(interfaces) + 1}", "region_ids": [first, second],
                         "segment_ids": segments,
                         "length_m": round(sum(float(index.segments[segment]["length_m"]) for segment in segments), 3)}
            interfaces.append(interface)
            for member, neighbor in ((first, second), (second, first)):
                active[member]["neighbors"].append({"region_id": neighbor, "interface_id": interface["id"],
                                                     "segment_ids": segments, "length_m": interface["length_m"]})
    regions = sorted(active.values(), key=lambda node: node["id"])
    atomic_owner = {member: region["id"] for region in regions for member in region["atomic_region_ids"]}
    base_owner = {member: region["id"] for region in regions for member in region["member_face_ids"]}
    if set(atomic_owner) != atomic_members or set(base_owner) != base_members:
        raise ValueError("Microcycle repair lost atomic membership")
    canonical = {key: value for key, value in inventory.items() if key != "timings_seconds"}
    counts = Counter(region["status"] for region in regions)
    result = {
        "schema_version": 1, "kind": "microcycle-partition",
        "source_atomic_inventory_sha256": hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "effective_edge_profile": profile,
        "settings": {"maximum_atomic_regions_per_microcycle": maximum_atoms, "maximum_merges": maximum_merges,
                     "minimum_convexity_improvement": minimum_gain, "boundary_policy": boundary_settings},
        "start_node_id": inventory["start_node_id"], "source_bounds_epsg3067": inventory["source_bounds_epsg3067"],
        "source_topology_summary": inventory["source_topology_summary"],
        "source_topology_diagnostics": inventory["source_topology_diagnostics"],
        "source_atomic_summary": inventory["summary"],
        "nodes": sorted(nodes.values(), key=lambda node: node["id"]), "regions": regions,
        "active_region_ids": [region["id"] for region in regions],
        "atomic_region_to_microcycle": dict(sorted(atomic_owner.items())),
        "base_face_to_microcycle": dict(sorted(base_owner.items())),
        "shared_interfaces": interfaces, "repair_events": events, "merge_attempts": attempts,
        "ranking_fields": list(RANK_FIELDS),
        "summary": {"atomic_region_count": len(atomic_members), "base_face_count": len(base_members),
                    "active_region_count": len(regions), "node_count": len(nodes),
                    "classification_counts": {status: counts[status] for status in STATUSES},
                    "merged_count": len(events),
                    "repaired_count": sum(bool(region["children"]) and region["status"] == "accepted" for region in regions),
                    "absorbed_atomic_region_count": sum(len(region["atomic_region_ids"]) for region in regions if region["children"]),
                    "merge_attempt_count": len(attempts), "shared_interface_count": len(interfaces),
                    "unreachable_count": sum(not region["reachability"]["round_trip_reachable"] for region in regions),
                    "membership_verified": True, "hierarchy_verified": True},
        "rejection_summary": dict(sorted(Counter(reason for region in regions for reason in
                                                  region["rejection_reasons"] + region["quality_reasons"] + region["standalone_rejection_reasons"]).items())),
        "merge_rejection_summary": dict(sorted(Counter(reason for attempt in attempts for reason in attempt["rejection_reasons"]).items())),
        "timings_seconds": {"topology": round(topology_time, 6), "access_distances": round(access_time, 6), "validation": round(validation_time, 6),
                            "metrics": round(metric_time, 6), "total": round(time.perf_counter() - started, 6)},
    }
    verification_started = time.perf_counter()
    verify_microcycle_partition(index, result)
    result["timings_seconds"]["validation"] = round(validation_time + time.perf_counter() - verification_started, 6)
    result["timings_seconds"]["total"] = round(time.perf_counter() - started, 6)
    return result


def microcycle_partition_geojson(index, partition):
    geojson = atomic_regions_geojson(index, partition)
    for feature, region in zip(geojson["features"], partition["regions"]):
        feature["properties"].update({key: region[key] for key in (
            "atomic_region_ids", "children", "level", "merge_interface", "repair_sequence", "repair_outcome",
        )})
    return geojson


def write_microcycle_outputs(index, partition, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        "partition": (directory / "microcycle-partition.json", partition),
        "geojson": (directory / "microcycle-partition.geojson", microcycle_partition_geojson(index, partition)),
        "summary": (directory / "microcycle-partition-summary.json", {
            key: partition[key] for key in ("summary", "source_atomic_summary", "rejection_summary",
                                            "merge_rejection_summary", "timings_seconds")
        }),
    }
    for path, document in outputs.values():
        path.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
    return {name: path for name, (path, _) in outputs.items()}

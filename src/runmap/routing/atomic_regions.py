"""Auditable, disjoint atomic inventory for continuous cycle refinement."""

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import time

from rasterio.warp import transform

from runmap.routing.face_search import (
    _convex_hull, _polygon_area, _reverse_order, ordered_boundary,
)
from runmap.routing.profiles import _tag, dissolve_forbidden_separators, effective_edge_profile
from runmap.routing.search import (
    _line_segments_intersect, route_metrics, shortest_distances,
)


STATUSES = ("accepted", "repair-needed", "connector-only", "invalid")


def verify_face_incidences(index, embedding):
    """Reject corrupt source boundaries or a dual that disagrees with them."""
    incidences = defaultdict(list)
    face_ids = set()
    directed = {}
    for face in embedding["faces"]:
        face_id = face["id"]
        if face_id in face_ids:
            raise ValueError(f"Duplicate base face: {face_id}")
        face_ids.add(face_id)
        segments = face["segment_ids"]
        if len(segments) != len(set(segments)) or ordered_boundary(index, segments) is None:
            raise ValueError(f"Base face {face_id} is not a single simple boundary")
        nodes = face["node_ids"]
        if len(nodes) != len(segments) or len(set(nodes)) != len(nodes):
            raise ValueError(f"Invalid node incidence for {face_id}")
        for offset, segment_id in enumerate(segments):
            segment = index.segments[segment_id]
            first, second = nodes[offset], nodes[(offset + 1) % len(nodes)]
            if {first, second} != {segment["from"], segment["to"]}:
                raise ValueError(f"Invalid boundary incidence: {face_id}/{segment_id}")
            half_edges = face.get("half_edge_ids")
            if half_edges is not None:
                expected = f"{segment_id}:{'f' if first == segment['from'] else 'b'}"
                valid_ids = {expected}
                if first == second:
                    valid_ids.add(f"{segment_id}:b")
                if len(half_edges) != len(segments) or half_edges[offset] not in valid_ids:
                    raise ValueError(f"Invalid half-edge incidence: {face_id}/{segment_id}")
            incidences[segment_id].append(face_id)
            directed[face_id, segment_id] = (first, second)
    expected = set()
    for segment_id, members in incidences.items():
        if len(members) > 2:
            raise ValueError(f"Non-manifold segment incidence: {segment_id}")
        if len(members) == 2:
            first, second = sorted(members)
            if directed[first, segment_id] != directed[second, segment_id][::-1]:
                raise ValueError(f"Shared half-edges are not twins: {segment_id}")
            expected.add((segment_id, first, second))
    actual = []
    for item in embedding["face_adjacency"]:
        if len(item["faces"]) != 2:
            raise ValueError("Dual interface must have two faces")
        actual.append((item["segment_id"], *sorted(item["faces"])))
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Face dual incidences disagree with boundary incidences")
    return incidences


def _boundary_lines(index, ordered):
    lines = []
    for segment_id, first, _ in ordered:
        segment = index.segments[segment_id]
        line = [list(point) for point in segment["geometry"]]
        if first != segment["from"]:
            line.reverse()
        lines.append(line)
    return lines


def _boundary_components(index, segment_ids):
    adjacent = defaultdict(set)
    for segment_id in segment_ids:
        segment = index.segments[segment_id]
        for node in (segment["from"], segment["to"]):
            adjacent[node].add(segment_id)
    unseen = set(segment_ids)
    components = []
    while unseen:
        pending = [min(unseen)]
        component = set()
        while pending:
            segment_id = pending.pop()
            if segment_id in component:
                continue
            component.add(segment_id)
            segment = index.segments[segment_id]
            for node in (segment["from"], segment["to"]):
                pending.extend(adjacent[node] - component)
        unseen.difference_update(component)
        components.append(component)
    return components


def _ring_coordinates(index, ordered):
    coordinates = []
    for line in _boundary_lines(index, ordered):
        for point in line:
            if not coordinates or point != coordinates[-1]:
                coordinates.append(point)
    return coordinates


def _point_in_ring(point, ring):
    """Return containment with boundary points included for shared interfaces."""
    x, y = point
    inside = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:]):
        cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        if abs(cross) <= 1e-14 and min(ax, bx) - 1e-12 <= x <= max(ax, bx) + 1e-12 and min(ay, by) - 1e-12 <= y <= max(ay, by) + 1e-12:
            return True
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside
    return inside


def _fill_enclosed_holes(index, embedding, faces, forbidden, outward, returning):
    """Absorb complete enclosed profile regions; verify their exact face union."""
    base_faces = {face["id"]: face for face in embedding["faces"]}
    active = {face["id"]: dict(face, source_profile_region_ids=[face["id"]]) for face in faces}
    candidates = []
    for face in faces:
        components = _boundary_components(index, face["segment_ids"])
        if len(components) < 2:
            continue
        rings = []
        for component in components:
            ordered = ordered_boundary(index, component)
            if ordered is None:
                break
            coordinates = _ring_coordinates(index, ordered)
            xs, ys = transform("EPSG:4326", "EPSG:3067", *zip(*coordinates))
            rings.append((component, coordinates, _polygon_area(list(zip(xs, ys))[:-1])))
        if len(rings) == len(components):
            rings.sort(key=lambda ring: (-ring[2], tuple(sorted(ring[0]))))
            candidates.append((rings[0][2], face["id"], rings))
    fills, diagnostics = [], []
    # Larger envelopes claim their enclaves first, including nested envelopes.
    for _, region_id, rings in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if region_id not in active:
            continue
        outer, holes = rings[0], rings[1:]
        if not all(_point_in_ring(point, outer[1]) for hole in holes for point in hole[1]):
            diagnostics.append({"region_id": region_id, "reason": "disconnected-outer-boundaries"})
            continue
        enclosed = []
        for candidate_id, candidate in sorted(active.items()):
            if candidate_id == region_id:
                continue
            if all(any(all(_point_in_ring(point, hole[1]) for point in base_faces[member]["geometry"])
                       for hole in holes) for member in candidate["member_face_ids"]):
                enclosed.append(candidate)
        region = active[region_id]
        members = sorted(region["member_face_ids"] + [member for candidate in enclosed for member in candidate["member_face_ids"]])
        boundary = set()
        for member in members:
            boundary.symmetric_difference_update(base_faces[member]["segment_ids"])
        # No boundary is dropped unless adding its actual faces cancels it.
        if boundary != outer[0] or len(members) != len(set(members)):
            diagnostics.append({"region_id": region_id, "reason": "incomplete-hole-coverage"})
            continue
        absorbed_area = sum(candidate["area_m2"] for candidate in enclosed)
        area = region["area_m2"] + absorbed_area
        previous = [region, *enclosed]
        sources = sorted(source for candidate in previous for source in candidate["source_profile_region_ids"])
        fill = {
            "region_id": region_id, "source_profile_region_ids": sources,
            "absorbed_profile_region_ids": sorted(source for candidate in enclosed for source in candidate["source_profile_region_ids"]),
            "absorbed_face_ids": sorted(member for candidate in enclosed for member in candidate["member_face_ids"]),
            "absorbed_area_m2": round(absorbed_area, 3),
            "removed_boundary_segment_ids": sorted(set(region["segment_ids"]) - boundary),
            "holes": [{"boundary_segment_ids": sorted(hole[0]), "area_m2": round(hole[2], 3),
                       "round_trip_reachable": any(
                           node in outward and node in returning
                           for segment in hole[0]
                           for node in (index.segments[segment]["from"], index.segments[segment]["to"])
                       )} for hole in holes],
        }
        fills.append(fill)
        active[region_id] = dict(region,
            member_face_ids=members, segment_ids=sorted(boundary),
            node_ids=sorted({node for segment in boundary for node in (index.segments[segment]["from"], index.segments[segment]["to"])}),
            area_m2=round(area, 3),
            centroid=[sum(candidate["centroid"][axis] * candidate["area_m2"] for candidate in previous) / area for axis in (0, 1)],
            forbidden_boundary_segment_ids=sorted(boundary & set(forbidden)),
            source_profile_region_ids=sources,
        )
        for candidate in enclosed:
            del active[candidate["id"]]
    return sorted(active.values(), key=lambda face: face["id"]), fills, diagnostics


def _shape_metrics(index, ordered, area, threshold):
    lines = _boundary_lines(index, ordered)
    coordinates = []
    for line in lines:
        for point in line:
            if not coordinates or point != coordinates[-1]:
                coordinates.append(point)
    xs, ys = transform("EPSG:4326", "EPSG:3067", *zip(*coordinates))
    points = list(zip(xs, ys))
    edges = list(zip(points, points[1:]))
    simple = len(set(points[:-1])) == len(points) - 1 and points[0] == points[-1]
    if simple:
        simple = not any(
            _line_segments_intersect(*edges[first], *edges[second])
            for first in range(len(edges))
            for second in range(first + 2, len(edges))
            if not (first == 0 and second == len(edges) - 1)
        )
    # Use segment tangents at junctions, including the closing junction.
    turns = []
    for offset, line in enumerate(lines):
        following = lines[(offset + 1) % len(lines)]
        before = next((point for point in reversed(line[:-1]) if point != line[-1]), None)
        after = next((point for point in following[1:] if point != following[0]), None)
        if before is None or after is None:
            simple = False
            continue
        origin = line[-1]
        scale = math.cos(math.radians(origin[1]))
        incoming = math.atan2((origin[0] - before[0]) * scale, origin[1] - before[1])
        outgoing = math.atan2((after[0] - origin[0]) * scale, after[1] - origin[1])
        turns.append((math.degrees(outgoing - incoming) + 180) % 360 - 180)
    perimeter = sum(float(index.segments[item[0]]["length_m"]) for item in ordered)
    hull_area = _polygon_area(_convex_hull(points[:-1]))
    quotient = 4 * math.pi * area / perimeter ** 2 if perimeter > 0 else 0
    dominant = 1 if sum(turns) >= 0 else -1
    return {
        "compactness": round(1 / quotient, 6) if quotient > 0 else None,
        "isoperimetric_quotient": round(quotient, 6),
        "convexity_ratio": round(min(area / hull_area, 1), 6) if hull_area > 0 else 0,
        "junction_turn_count": sum(abs(turn) >= 15 for turn in turns),
        "concave_turn_count": sum(abs(turn) >= 15 and turn * dominant < 0 for turn in turns),
        "sharp_turn_count": sum(abs(turn) >= threshold for turn in turns),
        "maximum_turn_degrees": round(max(map(abs, turns), default=0), 3),
        "turn_penalty": round(sum((abs(turn) / 90) ** 2 for turn in turns), 6),
        "boundary_segment_count": len(ordered),
    }, simple


def _surface_metrics(index, boundary):
    unknown = [segment for segment in boundary if _tag(index.segments[segment], "surface") in {"", "unknown"}]
    known = sorted(set(boundary) - set(unknown))
    unknown_length = sum(float(index.segments[segment]["length_m"]) for segment in unknown)
    known_length = sum(float(index.segments[segment]["length_m"]) for segment in known)
    total = known_length + unknown_length
    return {
        "known_surface_segment_ids": known, "unknown_surface_segment_ids": sorted(unknown),
        "known_surface_distance_m": round(known_length, 3),
        "unknown_surface_distance_m": round(unknown_length, 3),
        "unknown_surface_fraction": round(unknown_length / total, 6) if total > 0 else 0,
        "has_known_surface": bool(known),
    }


def evaluate_region_boundary(index, boundary, area, profile, settings, forbidden, traversals):
    """Apply the same boundary policy to atomic regions and repair unions."""
    started = time.perf_counter()
    boundary = sorted(boundary)
    nodes = {node for segment in boundary for node in (
        index.segments[segment]["from"], index.segments[segment]["to"]
    )}
    surface = _surface_metrics(index, boundary)
    standalone = (["all-unknown-surface"] if profile.get("require_known_surface_on_boundary")
                  and not surface["has_known_surface"] else [])
    profile_rejections = [{"segment_id": segment, "reason": forbidden[segment]}
                          for segment in boundary if segment in forbidden]
    reasons = ["forbidden-boundary"] if profile_rejections else []
    if set(boundary) & index.boundary_blocked_segments:
        reasons.append("route-boundary")
    if nodes & index.blocked_nodes:
        reasons.append("blocked-node")
    ordered = ordered_boundary(index, boundary)
    if ordered is None:
        reasons.append("non-simple-boundary")
    if not math.isfinite(area) or area <= 0:
        reasons.append("non-positive-area")
    orientations = []
    if ordered:
        for name, direction in (("forward", ordered), ("reverse", _reverse_order(ordered))):
            arcs = []
            for segment, first, second in direction:
                options = traversals.get((segment, first, second), [])
                if first == second:
                    suffix = ":f" if name == "forward" else ":b"
                    options = [arc for arc in options if arc.endswith(suffix)]
                arcs.append(options[0] if options else None)
            if all(arc is not None for arc in arcs) and not reasons:
                orientations.append({"direction": name, "boundary_arc_ids": arcs})
        if not orientations and not reasons:
            reasons.append("no-legal-orientation")
    validation_time = time.perf_counter() - started
    started = time.perf_counter()
    shape = None
    if ordered and math.isfinite(area) and area > 0:
        shape, simple = _shape_metrics(index, ordered, area, settings["near_reversal_threshold_degrees"])
        if not simple:
            reasons.append("self-intersecting-boundary")
        if shape["sharp_turn_count"]:
            reasons.append("near-reversal")
    if reasons:
        orientations = []
    for orientation in orientations:
        orientation["route_metrics"] = route_metrics(index, orientation["boundary_arc_ids"])
    quality = (["low-convexity"] if shape and shape["convexity_ratio"] < settings["minimum_convexity_ratio"] else [])
    measurement = {
        "boundary_segment_ids": boundary,
        "ordered_half_edges": [{"segment_id": segment, "origin": first, "destination": second,
                                "id": f"{segment}:{'f' if first == index.segments[segment]['from'] else 'b'}"}
                               for segment, first, second in ordered or []],
        "area_m2": area,
        "perimeter_m": round(sum(float(index.segments[segment]["length_m"]) for segment in boundary), 3),
        "legal_orientations": orientations,
        "shape_metrics": shape, "surface_metrics": surface,
        "route_metrics": orientations[0]["route_metrics"] if orientations else None,
        "rejection_reasons": sorted(set(reasons)), "profile_rejections": profile_rejections,
        "quality_reasons": quality, "standalone_rejection_reasons": standalone,
        "status": "invalid" if reasons else "connector-only" if standalone else "repair-needed" if quality else "accepted",
    }
    return measurement, {"validation": validation_time, "metrics": time.perf_counter() - started}


def _articulations(adjacent):
    """Find connector vertices without recursion limits on large inventories."""
    discovery, low, parent, children = {}, {}, {}, Counter()
    result = set()
    for root in sorted(adjacent):
        if root in discovery:
            continue
        discovery[root] = low[root] = len(discovery)
        parent[root] = None
        stack = [(root, iter(sorted(adjacent[root])))]
        while stack:
            node, neighbors = stack[-1]
            neighbor = next(neighbors, None)
            if neighbor is None:
                stack.pop()
                previous = parent[node]
                if previous is None:
                    if children[node] > 1:
                        result.add(node)
                else:
                    low[previous] = min(low[previous], low[node])
                    if parent[previous] is not None and low[node] >= discovery[previous]:
                        result.add(previous)
                continue
            if neighbor not in discovery:
                parent[neighbor] = node
                children[node] += 1
                discovery[neighbor] = low[neighbor] = len(discovery)
                stack.append((neighbor, iter(sorted(adjacent[neighbor]))))
            elif neighbor != parent[node]:
                low[node] = min(low[node], discovery[neighbor])
    return result


def build_atomic_region_inventory(index, topology, face_settings=None, settings=None):
    """Inventory every profile must-link component, independent of route length."""
    started = time.perf_counter()
    face_settings, settings = dict(face_settings or {}), dict(settings or {})
    fill_holes = settings.get("fill_enclosed_holes", True)
    if not isinstance(fill_holes, bool):
        raise ValueError("fill_enclosed_holes must be a boolean")
    convexity_floor = float(settings.get("minimum_convexity_ratio", 0.25))
    threshold = float(face_settings.get("turns", {}).get("sharp_threshold_degrees", 170))
    if not math.isfinite(convexity_floor) or not 0 <= convexity_floor <= 1:
        raise ValueError("Atomic convexity floor must be a finite number between zero and one")
    if not math.isfinite(threshold) or not 0 < threshold <= 180:
        raise ValueError("Near-reversal threshold must be between zero and 180 degrees")
    profile = effective_edge_profile(face_settings.get("edge_profile"), face_settings.get("stairs"))
    profile = profile or {"id": "default"}
    require_known_surface = profile.get("require_known_surface_on_boundary", False)
    if not isinstance(require_known_surface, bool):
        raise ValueError("require_known_surface_on_boundary must be a boolean")
    embedding = topology["face_embedding"]
    verify_face_incidences(index, embedding)
    dissolved = dissolve_forbidden_separators(index, embedding, profile)
    faces = sorted(dissolved["faces"] + dissolved["invalid_faces"], key=lambda face: face["id"])
    owner = {}
    for face in faces:
        for member in face["member_face_ids"]:
            if member in owner:
                raise ValueError(f"Overlapping atomic membership: {member}")
            owner[member] = face["id"]
    if set(owner) != {face["id"] for face in embedding["faces"]}:
        raise ValueError("Atomic regions do not partition the retained base faces")
    profile_owner = dict(owner)
    topology_time = time.perf_counter() - started
    forbidden = dissolved["forbidden_segment_reasons"]
    blocked = set(forbidden) | index.boundary_blocked_segments
    start = index.document["start_snap"]["id"]
    access_started = time.perf_counter()
    outward = shortest_distances(index, start, blocked_segments=blocked)
    returning = shortest_distances(index, start, blocked_segments=blocked, reverse=True)
    access_time = time.perf_counter() - access_started
    filling_started = time.perf_counter()
    fills, fill_diagnostics = [], []
    if fill_holes:
        faces, fills, fill_diagnostics = _fill_enclosed_holes(
            index, embedding, faces, forbidden, outward, returning
        )
    owner = {}
    for face in faces:
        for member in face["member_face_ids"]:
            if member in owner:
                raise ValueError(f"Hole filling duplicated atomic membership: {member}")
            owner[member] = face["id"]
    if set(owner) != set(profile_owner):
        raise ValueError("Hole filling lost retained base faces")
    filling_time = time.perf_counter() - filling_started
    traversals = defaultdict(list)
    for arc_id, arc in sorted(index.arcs.items()):
        traversals[arc["segment_id"], arc["from"], arc["to"]].append(arc_id)
    regions = []
    validation_time = metric_time = 0.0
    boundary_settings = {"minimum_convexity_ratio": convexity_floor,
                         "near_reversal_threshold_degrees": threshold}
    for face in faces:
        measurement, timings = evaluate_region_boundary(
            index, face["segment_ids"], face["area_m2"], profile,
            boundary_settings, forbidden, traversals,
        )
        validation_time += timings["validation"]
        metric_time += timings["metrics"]
        nodes = face["node_ids"]
        portals = [{"node_id": node, "outward_distance_m": round(outward[node], 3),
                    "return_distance_m": round(returning[node], 3)}
                   for node in nodes if node in outward and node in returning]
        portals.sort(key=lambda portal: (portal["outward_distance_m"] + portal["return_distance_m"], portal["node_id"]))
        regions.append({
            **measurement,
            "id": face["id"], "level": 0,
            "member_face_ids": face["member_face_ids"],
            "atomic_region_ids": [face["id"]],
            "source_profile_region_ids": face.get("source_profile_region_ids", [face["id"]]),
            "hole_fills": [fill for fill in fills if fill["region_id"] == face["id"]],
            "children": [], "parent": None, "root_id": None, "merge_interface": None,
            "centroid": face["centroid"],
            "reachability": {
                "outward_reachable": any(node in outward for node in nodes),
                "return_reachable": any(node in returning for node in nodes),
                "round_trip_reachable": bool(portals), "portals": portals,
            },
            "neighbors": [],
        })
    by_id = {region["id"]: region for region in regions}
    interface_segments = defaultdict(set)
    for item in embedding["face_adjacency"]:
        first, second = sorted(owner[member] for member in item["faces"])
        if first != second:
            interface_segments[first, second].add(item["segment_id"])
    interfaces, excluded = [], []
    adjacent = {region["id"]: set() for region in regions if region["status"] != "invalid"}
    for (first, second), segments in sorted(interface_segments.items()):
        runnable = sorted(segments - blocked)
        unusable = [member for member in (first, second) if by_id[member]["status"] == "invalid"]
        if not runnable or unusable:
            excluded.append({"region_ids": [first, second], "segment_ids": sorted(segments),
                             "invalid_region_ids": unusable,
                             "reason": "invalid-neighbor" if unusable else "forbidden-interface"})
            continue
        if any(not set(runnable) <= set(by_id[member]["boundary_segment_ids"]) for member in (first, second)):
            raise ValueError("Shared interface is missing from an atomic boundary")
        interface = {"id": f"atomic-interface-{len(interfaces) + 1}", "region_ids": [first, second],
                     "segment_ids": runnable,
                     "length_m": round(sum(float(index.segments[segment]["length_m"]) for segment in runnable), 3)}
        interfaces.append(interface)
        for member, neighbor in ((first, second), (second, first)):
            adjacent[member].add(neighbor)
            by_id[member]["neighbors"].append({"region_id": neighbor, "interface_id": interface["id"],
                                               "segment_ids": runnable, "length_m": interface["length_m"]})
    for member in sorted(_articulations(adjacent)):
        if by_id[member]["status"] == "repair-needed":
            by_id[member]["status"] = "connector-only"
            by_id[member]["quality_reasons"].append("adjacency-articulation")
    for interface in interfaces:
        first, second = interface["region_ids"]
        for member, neighbor in ((first, second), (second, first)):
            matches = [item for item in by_id[member]["neighbors"] if item["interface_id"] == interface["id"]]
            if len(matches) != 1 or matches[0] != {
                "region_id": neighbor, "interface_id": interface["id"],
                "segment_ids": interface["segment_ids"], "length_m": interface["length_m"],
            }:
                raise ValueError("Atomic shared interfaces are not symmetric")
    counts = Counter(region["status"] for region in regions)
    return {
        "schema_version": 1, "kind": "atomic-region-inventory",
        "effective_edge_profile": profile,
        "settings": {"minimum_convexity_ratio": convexity_floor, "near_reversal_threshold_degrees": threshold,
                     "fill_enclosed_holes": fill_holes},
        "start_node_id": start,
        "source_bounds_epsg3067": embedding.get("bounds_epsg3067"),
        "source_topology_summary": topology.get("summary", {}),
        "source_topology_diagnostics": {key: embedding[key] for key in (
            "excluded_grade_separated_segments", "locally_pruned_segment_count",
            "local_bridge_segment_count", "rejected_open_walks",
            "rejected_non_simple_walks", "rejected_outer_or_degenerate_walks",
        ) if key in embedding},
        "profile_summary": dissolved["summary"],
        "base_face_to_atomic_region": dict(sorted(owner.items())),
        "base_face_to_profile_region": dict(sorted(profile_owner.items())),
        "hole_fills": fills, "hole_fill_diagnostics": fill_diagnostics,
        "regions": regions, "shared_interfaces": interfaces, "excluded_interfaces": excluded,
        "summary": {"base_face_count": len(owner), "atomic_region_count": len(regions),
                    "classification_counts": {status: counts[status] for status in STATUSES},
                    "shared_interface_count": len(interfaces),
                    "filled_hole_count": sum(len(fill["holes"]) for fill in fills),
                    "absorbed_profile_region_count": sum(len(fill["absorbed_profile_region_ids"]) for fill in fills),
                    "all_unknown_surface_count": sum(bool(region["standalone_rejection_reasons"]) for region in regions),
                    "unreachable_count": sum(not region["reachability"]["round_trip_reachable"] for region in regions),
                    "membership_verified": True, "incidences_verified": True,
                    "shared_interfaces_verified": True},
        "rejection_summary": dict(sorted(Counter(reason for region in regions
                                                   for reason in region["rejection_reasons"] + region["quality_reasons"] + region["standalone_rejection_reasons"]).items())),
        "timings_seconds": {"topology": round(topology_time, 6), "validation": round(validation_time, 6),
                            "metrics": round(metric_time, 6), "access_distances": round(access_time, 6),
                            "hole_filling": round(filling_time, 6),
                            "total": round(time.perf_counter() - started, 6)},
    }


def atomic_regions_geojson(index, inventory):
    """Include invalid boundaries as segment lines so no diagnostic region vanishes."""
    features = []
    for region in inventory["regions"]:
        properties = {key: region[key] for key in (
            "id", "member_face_ids", "status", "rejection_reasons", "quality_reasons",
            "profile_rejections", "area_m2", "perimeter_m", "shape_metrics",
            "source_profile_region_ids", "hole_fills",
            "surface_metrics", "standalone_rejection_reasons",
        )}
        features.append({"type": "Feature", "properties": properties, "geometry": {
            "type": "MultiLineString",
            "coordinates": [index.segments[segment]["geometry"] for segment in region["boundary_segment_ids"]],
        }})
    return {"type": "FeatureCollection", "features": features}


def write_atomic_region_outputs(index, inventory, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        "inventory": (directory / "atomic-regions.json", inventory),
        "geojson": (directory / "atomic-regions.geojson", atomic_regions_geojson(index, inventory)),
        "summary": (directory / "atomic-regions-summary.json", {
            "summary": inventory["summary"], "rejection_summary": inventory["rejection_summary"],
            "profile_summary": inventory["profile_summary"], "timings_seconds": inventory["timings_seconds"],
        }),
    }
    for path, document in outputs.values():
        path.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
    return {name: path for name, (path, _) in outputs.items()}

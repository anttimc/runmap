"""Undirected cyclic topology and planar half-edge face experiments."""

from collections import Counter, defaultdict, deque
import heapq
import json
import math
from pathlib import Path

from rasterio.warp import transform


def _eligible_segments(index):
    arc_segments = {arc["segment_id"] for arc in index.arcs.values()}
    return {
        segment_id: segment
        for segment_id, segment in index.segments.items()
        if segment_id in arc_segments
        and segment_id not in index.boundary_blocked_segments
        and segment["from"] not in index.blocked_nodes
        and segment["to"] not in index.blocked_nodes
    }


def _adjacency(segments):
    adjacency = defaultdict(list)
    for segment_id, segment in segments.items():
        adjacency[segment["from"]].append((segment_id, segment["to"]))
        adjacency[segment["to"]].append((segment_id, segment["from"]))
    return adjacency


def connectivity_topology(index):
    """Compute bridges, articulation points, biconnected blocks, and 2-core."""
    segments = _eligible_segments(index)
    self_loops = {
        segment_id: segment for segment_id, segment in segments.items()
        if segment["from"] == segment["to"]
    }
    full_adjacency = _adjacency(segments)
    adjacency = _adjacency({
        segment_id: segment for segment_id, segment in segments.items()
        if segment_id not in self_loops
    })
    discovery = {}
    low = {}
    parent = {}
    parent_edge = {}
    child_count = Counter()
    bridges = set()
    articulations = set()
    blocks = [
        {
            "segment_ids": [segment_id],
            "node_ids": [segment["from"]],
            "cycle_rank": 1,
        }
        for segment_id, segment in self_loops.items()
    ]
    clock = 0
    for root in adjacency:
        if root in discovery:
            continue
        edge_stack = []
        parent[root] = None
        parent_edge[root] = None
        clock += 1
        discovery[root] = low[root] = clock
        stack = [(root, 0)]
        while stack:
            node_id, offset = stack[-1]
            neighbours = adjacency[node_id]
            if offset < len(neighbours):
                segment_id, neighbour = neighbours[offset]
                stack[-1] = (node_id, offset + 1)
                if segment_id == parent_edge[node_id]:
                    continue
                if neighbour not in discovery:
                    parent[neighbour] = node_id
                    parent_edge[neighbour] = segment_id
                    child_count[node_id] += 1
                    clock += 1
                    discovery[neighbour] = low[neighbour] = clock
                    edge_stack.append(segment_id)
                    stack.append((neighbour, 0))
                elif discovery[neighbour] < discovery[node_id]:
                    low[node_id] = min(low[node_id], discovery[neighbour])
                    edge_stack.append(segment_id)
            else:
                stack.pop()
                ancestor = parent[node_id]
                if ancestor is None:
                    if child_count[node_id] > 1:
                        articulations.add(node_id)
                    if edge_stack:
                        blocks.append(_pop_block(edge_stack, None, segments))
                    continue
                low[ancestor] = min(low[ancestor], low[node_id])
                tree_edge = parent_edge[node_id]
                if low[node_id] > discovery[ancestor]:
                    bridges.add(tree_edge)
                if low[node_id] >= discovery[ancestor]:
                    if parent[ancestor] is not None or child_count[ancestor] > 1:
                        articulations.add(ancestor)
                    blocks.append(_pop_block(edge_stack, tree_edge, segments))

    core_segments, core_nodes = _two_core(segments, full_adjacency)
    cyclic_components = _cyclic_components(
        segments, core_segments - bridges
    )
    cyclic_blocks = [
        block for block in blocks
        if block["cycle_rank"] > 0
    ]
    return {
        "eligible_segment_ids": set(segments),
        "bridge_segment_ids": bridges,
        "articulation_node_ids": articulations,
        "core_segment_ids": core_segments,
        "core_node_ids": core_nodes,
        "biconnected_blocks": blocks,
        "cyclic_blocks": cyclic_blocks,
        "cyclic_components": cyclic_components,
    }


def _pop_block(edge_stack, final_edge, segments):
    edge_ids = []
    while edge_stack:
        segment_id = edge_stack.pop()
        edge_ids.append(segment_id)
        if segment_id == final_edge:
            break
    node_ids = {
        node_id
        for segment_id in edge_ids
        for node_id in (segments[segment_id]["from"], segments[segment_id]["to"])
    }
    cycle_rank = len(edge_ids) - len(node_ids) + 1 if node_ids else 0
    return {
        "segment_ids": edge_ids,
        "node_ids": sorted(node_ids, key=_sortable_id),
        "cycle_rank": cycle_rank,
    }


def _two_core(segments, adjacency):
    active_edges = set(segments)
    degrees = {node_id: len(edges) for node_id, edges in adjacency.items()}
    queue = deque(node_id for node_id, degree in degrees.items() if degree < 2)
    while queue:
        node_id = queue.popleft()
        if degrees.get(node_id, 0) >= 2:
            continue
        for segment_id, neighbour in adjacency.get(node_id, ()):
            if segment_id not in active_edges:
                continue
            active_edges.remove(segment_id)
            degrees[node_id] -= 1
            degrees[neighbour] -= 1
            if degrees[neighbour] == 1:
                queue.append(neighbour)
    active_nodes = {
        node_id for node_id, degree in degrees.items() if degree >= 2
    }
    return active_edges, active_nodes


def _bridge_ids(segments):
    adjacency = _adjacency(segments)
    discovery = {}
    low = {}
    parent_edge = {}
    bridges = set()
    clock = 0
    for root in adjacency:
        if root in discovery:
            continue
        parent_edge[root] = None
        clock += 1
        discovery[root] = low[root] = clock
        stack = [(root, None, 0)]
        while stack:
            node_id, parent, offset = stack[-1]
            neighbours = adjacency[node_id]
            if offset < len(neighbours):
                segment_id, neighbour = neighbours[offset]
                stack[-1] = (node_id, parent, offset + 1)
                if segment_id == parent_edge[node_id]:
                    continue
                if neighbour not in discovery:
                    parent_edge[neighbour] = segment_id
                    clock += 1
                    discovery[neighbour] = low[neighbour] = clock
                    stack.append((neighbour, node_id, 0))
                else:
                    low[node_id] = min(low[node_id], discovery[neighbour])
            else:
                stack.pop()
                if parent is None:
                    continue
                low[parent] = min(low[parent], low[node_id])
                if low[node_id] > discovery[parent]:
                    bridges.add(parent_edge[node_id])
    return bridges


def _cyclic_components(segments, selected_edges):
    adjacency = defaultdict(list)
    for segment_id in selected_edges:
        segment = segments[segment_id]
        adjacency[segment["from"]].append((segment_id, segment["to"]))
        adjacency[segment["to"]].append((segment_id, segment["from"]))
    unseen = set(adjacency)
    components = []
    while unseen:
        start = unseen.pop()
        queue = [start]
        nodes = set()
        edges = set()
        while queue:
            node_id = queue.pop()
            nodes.add(node_id)
            for segment_id, neighbour in adjacency[node_id]:
                edges.add(segment_id)
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    queue.append(neighbour)
        components.append({
            "node_ids": sorted(nodes, key=_sortable_id),
            "segment_ids": sorted(edges),
            "cycle_rank": len(edges) - len(nodes) + 1,
        })
    components.sort(key=lambda item: len(item["segment_ids"]), reverse=True)
    return components


def _sortable_id(value):
    try:
        return 0, int(value)
    except (TypeError, ValueError):
        return 1, str(value)


def enumerate_faces(index, connectivity, bounds_epsg3067):
    """Build a planar-test half-edge embedding and retain simple bounded faces."""
    west, south, east, north = map(float, bounds_epsg3067)
    node_ids = list(index.nodes)
    longitudes = [index.nodes[node_id]["longitude"] for node_id in node_ids]
    latitudes = [index.nodes[node_id]["latitude"] for node_id in node_ids]
    eastings, northings = transform(
        "EPSG:4326", "EPSG:3067", longitudes, latitudes
    )
    projected = {
        node_id: (x, y) for node_id, x, y in zip(node_ids, eastings, northings)
    }
    in_bounds = {
        node_id for node_id, (x, y) in projected.items()
        if west <= x <= east and south <= y <= north
    }
    candidate_segments = {}
    structural_segments = {}
    structural_reasons = {}
    excluded_by_layer = 0
    for segment_id in connectivity["eligible_segment_ids"]:
        segment = index.segments[segment_id]
        if segment["from"] not in in_bounds or segment["to"] not in in_bounds:
            continue
        tags = segment["tags"]
        reasons = _structure_reasons(tags)
        if reasons:
            excluded_by_layer += 1
            structural_segments[segment_id] = segment
            structural_reasons[segment_id] = reasons
            continue
        candidate_segments[segment_id] = segment

    candidate_adjacency = _adjacency(candidate_segments)
    local_core, _ = _two_core(candidate_segments, candidate_adjacency)
    local_bridges = _bridge_ids({
        segment_id: candidate_segments[segment_id] for segment_id in local_core
    })
    face_segments = {
        segment_id: candidate_segments[segment_id]
        for segment_id in local_core - local_bridges
    }

    half_edges = {}
    outgoing = defaultdict(list)
    for segment_id, segment in face_segments.items():
        forward_id = f"{segment_id}:f"
        backward_id = f"{segment_id}:b"
        half_edges[forward_id] = {
            "id": forward_id, "segment_id": segment_id,
            "origin": segment["from"], "destination": segment["to"],
            "twin": backward_id, "forward": True,
        }
        half_edges[backward_id] = {
            "id": backward_id, "segment_id": segment_id,
            "origin": segment["to"], "destination": segment["from"],
            "twin": forward_id, "forward": False,
        }
        outgoing[segment["from"]].append(forward_id)
        outgoing[segment["to"]].append(backward_id)
    for origin, edge_ids in outgoing.items():
        edge_ids.sort(key=lambda edge_id: _half_edge_angle(
            half_edges[edge_id], index.segments, projected
        ))

    visited = set()
    faces = []
    rejected_open = 0
    rejected_non_simple = 0
    rejected_outer = 0
    for start_id in half_edges:
        if start_id in visited:
            continue
        walk = []
        local = set()
        current_id = start_id
        while current_id not in local and current_id not in visited:
            local.add(current_id)
            walk.append(current_id)
            current = half_edges[current_id]
            around_destination = outgoing[current["destination"]]
            twin_offset = around_destination.index(current["twin"])
            current_id = around_destination[(twin_offset - 1) % len(around_destination)]
        visited.update(local)
        if current_id != start_id:
            rejected_open += 1
            continue
        origins = [half_edges[edge_id]["origin"] for edge_id in walk]
        if len(set(origins)) != len(origins):
            rejected_non_simple += 1
            continue
        coordinates = _face_coordinates(walk, half_edges, index.segments)
        if len(coordinates) < 4:
            rejected_outer += 1
            continue
        face_longitudes, face_latitudes = zip(*coordinates)
        face_east, face_north = transform(
            "EPSG:4326", "EPSG:3067", face_longitudes, face_latitudes
        )
        area = _signed_area(list(zip(face_east, face_north)))
        if area <= 1:
            rejected_outer += 1
            continue
        faces.append({
            "id": f"face-{len(faces) + 1}",
            "half_edge_ids": walk,
            "segment_ids": [half_edges[edge_id]["segment_id"] for edge_id in walk],
            "node_ids": origins,
            "perimeter_m": round(sum(
                float(index.segments[half_edges[edge_id]["segment_id"]]["length_m"])
                for edge_id in walk
            ), 3),
            "area_m2": round(area, 3),
            "geometry": coordinates,
        })

    segment_faces = defaultdict(list)
    for face in faces:
        for segment_id in face["segment_ids"]:
            segment_faces[segment_id].append(face["id"])
    adjacency = []
    for segment_id, face_ids in segment_faces.items():
        if len(face_ids) == 2:
            adjacency.append({
                "segment_id": segment_id,
                "faces": sorted(face_ids),
            })
    locally_pruned = {
        segment_id: segment for segment_id, segment in candidate_segments.items()
        if segment_id not in face_segments
    }
    connector_corridors = _connector_corridors(
        structural_segments,
        locally_pruned,
        structural_reasons,
        faces,
    )
    connector_segment_ids = {
        segment_id
        for corridor in connector_corridors
        for segment_id in corridor["segment_ids"]
    }
    return {
        "bounds_epsg3067": [west, south, east, north],
        "segment_ids": sorted(face_segments),
        "half_edge_count": len(half_edges),
        "faces": faces,
        "face_adjacency": adjacency,
        "connector_corridors": connector_corridors,
        "connector_segment_ids": sorted(connector_segment_ids),
        "excluded_grade_separated_segments": excluded_by_layer,
        "locally_pruned_segment_count": len(candidate_segments) - len(face_segments),
        "local_bridge_segment_count": len(local_bridges),
        "rejected_open_walks": rejected_open,
        "rejected_non_simple_walks": rejected_non_simple,
        "rejected_outer_or_degenerate_walks": rejected_outer,
    }


def _structure_reasons(tags):
    reasons = []
    if tags.get("bridge", "no") != "no":
        reasons.append("bridge")
    if tags.get("tunnel", "no") != "no":
        reasons.append("tunnel")
    if tags.get("covered", "no") != "no":
        reasons.append("covered")
    layer = str(tags.get("layer", "0"))
    if layer not in {"", "0"}:
        reasons.append(f"layer:{layer}")
    return reasons


def _connector_corridors(structural, locally_pruned, reasons, faces):
    """Group structures, adding only their shortest approaches to planar faces."""
    structural_adjacency = _adjacency(structural)
    approach_adjacency = _adjacency(locally_pruned)
    node_faces = defaultdict(set)
    for face in faces:
        for node_id in face["node_ids"]:
            node_faces[node_id].add(face["id"])
    unseen_edges = set(structural)
    corridors = []
    while unseen_edges:
        start_edge = min(unseen_edges)
        unseen_edges.remove(start_edge)
        start = structural[start_edge]
        queue = [start["from"], start["to"]]
        nodes = set()
        edges = {start_edge}
        while queue:
            node_id = queue.pop()
            if node_id in nodes:
                continue
            nodes.add(node_id)
            for segment_id, neighbour in structural_adjacency.get(node_id, ()):
                if segment_id not in edges:
                    edges.add(segment_id)
                    unseen_edges.discard(segment_id)
                if neighbour not in nodes:
                    queue.append(neighbour)
        structural_ids = sorted(edges)
        approach_ids = set()
        terminals = {
            node_id for node_id in nodes
            if len(structural_adjacency[node_id]) == 1
        }
        for terminal in terminals:
            if node_faces.get(terminal):
                continue
            approach_ids.update(_nearest_face_approach(
                terminal, approach_adjacency, locally_pruned, node_faces
            ))
        edges.update(approach_ids)
        all_nodes = {
            node_id
            for segment_id in edges
            for node_id in (
                (structural.get(segment_id) or locally_pruned[segment_id])["from"],
                (structural.get(segment_id) or locally_pruned[segment_id])["to"],
            )
        }
        attached_faces = sorted({
            face_id for node_id in all_nodes for face_id in node_faces.get(node_id, ())
        })
        corridors.append({
            "segment_ids": sorted(edges),
            "structural_segment_ids": structural_ids,
            "approach_segment_ids": sorted(approach_ids),
            "node_ids": sorted(all_nodes, key=_sortable_id),
            "attached_face_ids": attached_faces,
            "structure_reasons": sorted({
                reason for segment_id in structural_ids for reason in reasons[segment_id]
            }),
            "length_m": round(sum(float(
                (structural.get(segment_id) or locally_pruned[segment_id])["length_m"]
            ) for segment_id in edges), 3),
        })
    corridors.sort(key=lambda item: (-len(item["segment_ids"]), item["segment_ids"][0]))
    for offset, corridor in enumerate(corridors, 1):
        corridor["id"] = f"connector-{offset}"
    return corridors


def _nearest_face_approach(
    start_node, adjacency, segments, node_faces, max_distance_m=500.0
):
    """Return the shortest pruned-edge path from a structure end to a face."""
    queue = [(0.0, _sortable_id(start_node), start_node, ())]
    best = {start_node: 0.0}
    while queue:
        distance, _, node_id, path = heapq.heappop(queue)
        if distance != best[node_id]:
            continue
        if node_id != start_node and node_faces.get(node_id):
            return path
        for segment_id, neighbour in adjacency.get(node_id, ()):
            next_distance = distance + float(segments[segment_id]["length_m"])
            if next_distance > max_distance_m or next_distance >= best.get(neighbour, math.inf):
                continue
            best[neighbour] = next_distance
            heapq.heappush(
                queue,
                (next_distance, _sortable_id(neighbour), neighbour, path + (segment_id,)),
            )
    return ()


def _half_edge_angle(half_edge, segments, projected):
    segment = segments[half_edge["segment_id"]]
    geometry = segment["geometry"]
    if not half_edge["forward"]:
        geometry = list(reversed(geometry))
    origin = geometry[0]
    direction = next(
        (coordinate for coordinate in geometry[1:] if coordinate != origin),
        geometry[-1],
    )
    east = (direction[0] - origin[0]) * math.cos(math.radians(origin[1]))
    north = direction[1] - origin[1]
    return math.atan2(north, east)


def _face_coordinates(walk, half_edges, segments):
    coordinates = []
    for edge_id in walk:
        half_edge = half_edges[edge_id]
        geometry = segments[half_edge["segment_id"]]["geometry"]
        if not half_edge["forward"]:
            geometry = reversed(geometry)
        for coordinate in geometry:
            if not coordinates or coordinate != coordinates[-1]:
                coordinates.append(coordinate)
    if coordinates and coordinates[0] != coordinates[-1]:
        coordinates.append(coordinates[0])
    return coordinates


def _signed_area(coordinates):
    return 0.5 * sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(coordinates, coordinates[1:])
    )


def prepare_topology(index, face_bounds):
    connectivity = connectivity_topology(index)
    embedding = enumerate_faces(index, connectivity, face_bounds)
    return {
        "schema_version": 1,
        "summary": {
            "eligible_segment_count": len(connectivity["eligible_segment_ids"]),
            "bridge_segment_count": len(connectivity["bridge_segment_ids"]),
            "articulation_node_count": len(connectivity["articulation_node_ids"]),
            "core_segment_count": len(connectivity["core_segment_ids"]),
            "core_node_count": len(connectivity["core_node_ids"]),
            "biconnected_block_count": len(connectivity["biconnected_blocks"]),
            "cyclic_block_count": len(connectivity["cyclic_blocks"]),
            "cyclic_component_count": len(connectivity["cyclic_components"]),
            "largest_cyclic_component_segments": (
                len(connectivity["cyclic_components"][0]["segment_ids"])
                if connectivity["cyclic_components"] else 0
            ),
            "face_test_segment_count": len(embedding["segment_ids"]),
            "half_edge_count": embedding["half_edge_count"],
            "bounded_face_count": len(embedding["faces"]),
            "connector_corridor_count": len(embedding["connector_corridors"]),
            "connector_segment_count": len(embedding["connector_segment_ids"]),
            "connector_corridors_with_multiple_faces": sum(
                len(corridor["attached_face_ids"]) >= 2
                for corridor in embedding["connector_corridors"]
            ),
        },
        "bridge_segment_ids": sorted(connectivity["bridge_segment_ids"]),
        "articulation_node_ids": sorted(
            connectivity["articulation_node_ids"], key=_sortable_id
        ),
        "core_segment_ids": sorted(connectivity["core_segment_ids"]),
        "core_node_ids": sorted(connectivity["core_node_ids"], key=_sortable_id),
        "biconnected_blocks": connectivity["biconnected_blocks"],
        "cyclic_components": connectivity["cyclic_components"],
        "face_embedding": embedding,
    }


def topology_geojson(index, topology):
    bridge_ids = set(topology["bridge_segment_ids"])
    core_ids = set(topology["core_segment_ids"])
    component_by_segment = {}
    face_segment_ids = set(topology["face_embedding"]["segment_ids"])
    connector_segment_ids = set(topology["face_embedding"]["connector_segment_ids"])
    for offset, component in enumerate(topology["cyclic_components"], 1):
        for segment_id in component["segment_ids"]:
            component_by_segment[segment_id] = offset
    features = []
    for segment_id in sorted(_eligible_segments(index)):
        segment = index.segments[segment_id]
        features.append({
            "type": "Feature",
            "id": segment_id,
            "geometry": {"type": "LineString", "coordinates": segment["geometry"]},
            "properties": {
                "segment_id": segment_id,
                "bridge": segment_id in bridge_ids,
                "in_2_core": segment_id in core_ids,
                "cyclic_component": component_by_segment.get(segment_id),
                "in_face_embedding": segment_id in face_segment_ids,
                "face_connector": segment_id in connector_segment_ids,
                "structure": ",".join(_structure_reasons(segment["tags"])) or None,
                "highway": segment["tags"].get("highway"),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def faces_geojson(index, topology):
    features = [
        {
            "type": "Feature",
            "id": face["id"],
            "geometry": {"type": "Polygon", "coordinates": [face["geometry"]]},
            "properties": {
                "feature_type": "face",
                "face_id": face["id"],
                "area_m2": face["area_m2"],
                "perimeter_m": face["perimeter_m"],
                "edge_count": len(face["segment_ids"]),
            },
        }
        for face in topology["face_embedding"]["faces"]
    ]
    corridor_by_segment = {
        segment_id: corridor
        for corridor in topology["face_embedding"]["connector_corridors"]
        for segment_id in corridor["segment_ids"]
    }
    for segment_id in topology["face_embedding"]["connector_segment_ids"]:
        corridor = corridor_by_segment[segment_id]
        features.append({
            "type": "Feature",
            "id": segment_id,
            "geometry": {
                "type": "LineString",
                "coordinates": index.segments[segment_id]["geometry"],
            },
            "properties": {
                "feature_type": "connector",
                "segment_id": segment_id,
                "corridor_id": corridor["id"],
                "structural": segment_id in corridor["structural_segment_ids"],
                "structure": ",".join(_structure_reasons(index.segments[segment_id]["tags"])),
                "attached_face_ids": corridor["attached_face_ids"],
                "corridor_structure_reasons": corridor["structure_reasons"],
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def symmetric_difference_boundary(face_ids, face_embedding):
    """Return physical segments selected an odd number of times by faces."""
    requested = set(face_ids)
    available = {face["id"]: face for face in face_embedding["faces"]}
    missing = requested - available.keys()
    if missing:
        raise ValueError(f"Unknown faces: {', '.join(sorted(missing))}")
    boundary = set()
    for face_id in requested:
        for segment_id in available[face_id]["segment_ids"]:
            if segment_id in boundary:
                boundary.remove(segment_id)
            else:
                boundary.add(segment_id)
    return boundary


def write_topology_outputs(index, topology, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        "topology": (directory / "topology.json", topology),
        "segments": (directory / "topology-segments.geojson", topology_geojson(index, topology)),
        "faces": (directory / "faces.geojson", faces_geojson(index, topology)),
    }
    for path, document in outputs.values():
        partial = path.with_suffix(path.suffix + ".part")
        partial.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
        partial.replace(path)
    return {name: path for name, (path, _) in outputs.items()}

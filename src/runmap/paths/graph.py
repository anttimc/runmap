"""Normalize OSM highway ways into an inspectable pedestrian routing graph."""

from collections import Counter, defaultdict, deque
import json
import math
from pathlib import Path


GRAPH_SCHEMA_VERSION = 1
RUNNABLE_HIGHWAYS = frozenset({
    "cycleway",
    "footway",
    "living_street",
    "path",
    "pedestrian",
    "residential",
    "service",
    "steps",
    "track",
    "unclassified",
})
_DENIED = frozenset({"no", "private"})
_ALLOWED = frozenset({"yes", "designated", "permissive", "official"})
_TRUE = frozenset({"yes", "true", "1"})
_FALSE = frozenset({"no", "false", "0"})


def _tag(tags, name):
    value = tags.get(name)
    return str(value).strip().lower() if value is not None else ""


def runnable_reason(tags):
    """Return (included, reason) for the deliberately narrow first policy."""
    highway = _tag(tags, "highway")
    if highway not in RUNNABLE_HIGHWAYS:
        return False, "unsupported_highway"
    if _tag(tags, "area") in _TRUE:
        return False, "area_geometry"
    if _tag(tags, "indoor") in _TRUE:
        return False, "indoor"
    if _tag(tags, "construction") or highway in {"construction", "proposed"}:
        return False, "not_in_service"
    foot = _tag(tags, "foot")
    access = _tag(tags, "access")
    if foot in _DENIED:
        return False, "foot_denied"
    if highway == "cycleway" and foot not in _ALLOWED:
        return False, "cycleway_without_foot_permission"
    if access in _DENIED and foot not in _ALLOWED:
        return False, "access_denied"
    return True, "included"


def _direction_permissions(tags):
    """Return forward/backward pedestrian permissions for an OSM way."""
    forward = _tag(tags, "foot:forward") not in _DENIED
    backward = _tag(tags, "foot:backward") not in _DENIED
    foot_oneway = _tag(tags, "oneway:foot")
    if foot_oneway in _TRUE:
        backward = False
    elif foot_oneway == "-1":
        forward = False
    return forward, backward


def _haversine_m(first, second):
    lon1, lat1 = map(math.radians, first)
    lon2, lat2 = map(math.radians, second)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(value))


def geometry_length_m(coordinates):
    return sum(_haversine_m(first, second) for first, second in zip(coordinates, coordinates[1:]))


def _selected_tags(tags):
    names = (
        "highway", "name", "service", "surface", "smoothness", "tracktype", "access",
        "foot", "bicycle", "segregated", "foot:forward", "foot:backward",
        "oneway", "oneway:foot", "lit",
        "bridge", "tunnel", "layer", "indoor", "covered", "sac_scale",
    )
    return {name: str(tags[name]) for name in names if name in tags}


def _node_record(element):
    tags = element.get("tags", {})
    record = {
        "id": str(element["id"]),
        "longitude": float(element["lon"]),
        "latitude": float(element["lat"]),
    }
    for name in ("barrier", "access", "foot", "entrance", "crossing", "highway"):
        if name in tags:
            record[name] = str(tags[name])
    return record


def build_routing_graph(osm_document, source=None):
    """Build directed arcs while retaining intermediate OSM geometry points."""
    elements = osm_document.get("elements")
    if not isinstance(elements, list):
        raise ValueError("OSM document must contain an elements list")
    raw_nodes = {
        int(element["id"]): element
        for element in elements
        if element.get("type") == "node" and "lat" in element and "lon" in element
    }
    ways = [element for element in elements if element.get("type") == "way"]
    rejection_counts = Counter()
    selected_ways = []
    for way in ways:
        included, reason = runnable_reason(way.get("tags", {}))
        if not included:
            rejection_counts[reason] += 1
            continue
        node_ids = way.get("nodes", [])
        if len(node_ids) < 2 or any(node_id not in raw_nodes for node_id in node_ids):
            rejection_counts["incomplete_geometry"] += 1
            continue
        selected_ways.append(way)

    occurrence = Counter(node_id for way in selected_ways for node_id in set(way["nodes"]))
    junctions = set()
    for way in selected_ways:
        junctions.update((way["nodes"][0], way["nodes"][-1]))
    junctions.update(node_id for node_id, count in occurrence.items() if count > 1)
    junctions.update(
        node_id for node_id in occurrence
        if raw_nodes[node_id].get("tags")
    )

    graph_nodes = {}
    segments = []
    arcs = []
    highway_counts = Counter()
    for way in selected_ways:
        tags = way.get("tags", {})
        way_nodes = way["nodes"]
        forward, backward = _direction_permissions(tags)
        start_index = 0
        part = 0
        for index in range(1, len(way_nodes)):
            if way_nodes[index] not in junctions:
                continue
            ids = way_nodes[start_index:index + 1]
            start_index = index
            if len(ids) < 2:
                continue
            coordinates = [
                [float(raw_nodes[node_id]["lon"]), float(raw_nodes[node_id]["lat"])]
                for node_id in ids
            ]
            length_m = geometry_length_m(coordinates)
            if length_m <= 0:
                continue
            for node_id in (ids[0], ids[-1]):
                graph_nodes[str(node_id)] = _node_record(raw_nodes[node_id])
            segment_id = f"w{way['id']}p{part}"
            segment = {
                "id": segment_id,
                "osm_way_id": str(way["id"]),
                "from": str(ids[0]),
                "to": str(ids[-1]),
                "length_m": round(length_m, 3),
                "geometry": coordinates,
                "tags": _selected_tags(tags),
                "forward": forward,
                "backward": backward,
            }
            segments.append(segment)
            highway_counts[_tag(tags, "highway")] += 1
            if forward:
                arcs.append(_arc(segment, "forward"))
            if backward:
                arcs.append(_arc(segment, "backward"))
            part += 1

    components = _component_sizes(graph_nodes, segments)
    summary = {
        "source_node_count": len(raw_nodes),
        "source_way_count": len(ways),
        "selected_way_count": len(selected_ways),
        "rejected_way_counts": dict(sorted(rejection_counts.items())),
        "node_count": len(graph_nodes),
        "segment_count": len(segments),
        "arc_count": len(arcs),
        "physical_length_km": round(sum(segment["length_m"] for segment in segments) / 1000, 3),
        "component_count": len(components),
        "largest_component_nodes": components[0] if components else 0,
        "highway_segment_counts": dict(sorted(highway_counts.items())),
        "missing_surface_segments": sum("surface" not in segment["tags"] for segment in segments),
        "bridge_segments": sum(_tag(segment["tags"], "bridge") not in {"", "no"} for segment in segments),
        "tunnel_segments": sum(_tag(segment["tags"], "tunnel") not in {"", "no"} for segment in segments),
        "stair_segments": highway_counts["steps"],
        "pedestrian_oneway_segments": sum(
            segment["forward"] != segment["backward"] for segment in segments
        ),
        "barrier_nodes": sum("barrier" in node for node in graph_nodes.values()),
    }
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "source": dict(source or {}),
        "nodes": [graph_nodes[key] for key in sorted(graph_nodes, key=int)],
        "segments": segments,
        "arcs": arcs,
        "summary": summary,
    }


def _arc(segment, direction):
    forward = direction == "forward"
    return {
        "id": f"{segment['id']}:{'f' if forward else 'b'}",
        "segment_id": segment["id"],
        "from": segment["from"] if forward else segment["to"],
        "to": segment["to"] if forward else segment["from"],
        "length_m": segment["length_m"],
    }


def _component_sizes(nodes, segments):
    adjacency = defaultdict(set)
    for segment in segments:
        adjacency[segment["from"]].add(segment["to"])
        adjacency[segment["to"]].add(segment["from"])
    unseen = set(nodes)
    sizes = []
    while unseen:
        start = unseen.pop()
        queue = deque([start])
        size = 0
        while queue:
            node = queue.popleft()
            size += 1
            for neighbour in adjacency[node]:
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    queue.append(neighbour)
        sizes.append(size)
    return sorted(sizes, reverse=True)


def snap_to_vertex(graph, longitude, latitude):
    """Return the nearest graph vertex and great-circle snap distance."""
    if not graph["nodes"]:
        raise ValueError("Cannot snap to an empty graph")
    target = (float(longitude), float(latitude))
    node, distance = min(
        (
            (node, _haversine_m(target, (node["longitude"], node["latitude"])))
            for node in graph["nodes"]
        ),
        key=lambda item: item[1],
    )
    incident = [
        segment["id"] for segment in graph["segments"]
        if node["id"] in {segment["from"], segment["to"]}
    ]
    return {
        **node,
        "snap_distance_m": round(distance, 3),
        "physical_degree": len(incident),
        "incident_segments": incident,
    }


def graph_geojson(graph):
    """Return physical segments once, with directionality as properties."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": segment["id"],
                "geometry": {"type": "LineString", "coordinates": segment["geometry"]},
                "properties": {
                    "segment_id": segment["id"],
                    "osm_way_id": segment["osm_way_id"],
                    "from": segment["from"],
                    "to": segment["to"],
                    "length_m": segment["length_m"],
                    "forward": segment["forward"],
                    "backward": segment["backward"],
                    **{
                        name: segment[name]
                        for name in (
                            "ascent_m", "descent_m", "terrain_valid_fraction"
                        )
                        if name in segment
                    },
                    **segment["tags"],
                },
            }
            for segment in graph["segments"]
        ],
    }


def write_graph_outputs(graph, directory):
    """Atomically write machine-readable graph and map-inspection GeoJSON."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        "graph": (directory / "graph.json", graph),
        "geojson": (directory / "segments.geojson", graph_geojson(graph)),
        "inspection": (
            directory / "inspection.json",
            {"summary": graph["summary"], "start_snap": graph.get("start_snap")},
        ),
    }
    for path, document in outputs.values():
        partial = path.with_suffix(path.suffix + ".part")
        partial.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
        partial.replace(path)
    return {name: path for name, (path, _) in outputs.items()}

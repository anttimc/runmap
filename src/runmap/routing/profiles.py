"""Route-profile edge filtering and profile-specific face dissolution."""

from collections import defaultdict


def _tag(segment, name):
    value = segment.get("tags", {}).get(name)
    return str(value).strip().lower() if value is not None else ""


def forbidden_reason(segment, profile):
    """Return a profile rejection reason, or None when the edge is usable."""
    highway = _tag(segment, "highway")
    surface = _tag(segment, "surface")
    if highway in set(profile.get("forbidden_highways", ())):
        return f"highway:{highway}"
    if surface in set(profile.get("forbidden_surfaces", ())):
        return f"surface:{surface}"
    if not surface and highway in set(profile.get("forbid_unknown_surface_highways", ())):
        return f"unknown_surface:{highway}"
    return None


def classify_segments(index, profile):
    reasons = {}
    for segment_id, segment in index.segments.items():
        reason = forbidden_reason(segment, profile)
        if reason:
            reasons[segment_id] = reason
    return reasons


def dissolve_forbidden_separators(index, embedding, profile):
    """Return a face embedding whose forbidden shared edges have been dissolved."""
    reasons = classify_segments(index, profile)
    forbidden = set(reasons)
    faces = {face["id"]: face for face in embedding["faces"]}
    parent = {face_id: face_id for face_id in faces}

    def find(face_id):
        while parent[face_id] != face_id:
            parent[face_id] = parent[parent[face_id]]
            face_id = parent[face_id]
        return face_id

    def union(first, second):
        first, second = find(first), find(second)
        if first != second:
            parent[max(first, second)] = min(first, second)

    dissolved = set()
    for item in embedding["face_adjacency"]:
        if item["segment_id"] in forbidden:
            union(*item["faces"])
            dissolved.add(item["segment_id"])

    groups = defaultdict(list)
    for face_id in sorted(faces):
        groups[find(face_id)].append(face_id)
    face_to_super = {}
    superfaces = []
    invalid = []
    for offset, members in enumerate(sorted(groups.values()), 1):
        boundary = set()
        area = 0.0
        weighted_lon = 0.0
        weighted_lat = 0.0
        for face_id in members:
            item = faces[face_id]
            area += float(item["area_m2"])
            coordinates = item["geometry"][:-1] or item["geometry"]
            centroid = (
                sum(point[0] for point in coordinates) / len(coordinates),
                sum(point[1] for point in coordinates) / len(coordinates),
            )
            weighted_lon += centroid[0] * float(item["area_m2"])
            weighted_lat += centroid[1] * float(item["area_m2"])
            for segment_id in item["segment_ids"]:
                if segment_id in boundary:
                    boundary.remove(segment_id)
                else:
                    boundary.add(segment_id)
        super_id = f"profile-face-{offset}"
        for face_id in members:
            face_to_super[face_id] = super_id
        forbidden_boundary = sorted(boundary & forbidden)
        item = {
            "id": super_id,
            "member_face_ids": members,
            "segment_ids": sorted(boundary),
            "node_ids": sorted({
                node_id
                for segment_id in boundary
                for node_id in (
                    index.segments[segment_id]["from"],
                    index.segments[segment_id]["to"],
                )
            }),
            "area_m2": round(area, 3),
            "centroid": [weighted_lon / area, weighted_lat / area],
            "forbidden_boundary_segment_ids": forbidden_boundary,
        }
        if forbidden_boundary:
            invalid.append(item)
        else:
            superfaces.append(item)

    adjacency = []
    seen = set()
    valid_ids = {item["id"] for item in superfaces}
    for item in embedding["face_adjacency"]:
        if item["segment_id"] in forbidden:
            continue
        first, second = (face_to_super[face_id] for face_id in item["faces"])
        if first == second or first not in valid_ids or second not in valid_ids:
            continue
        key = (item["segment_id"], min(first, second), max(first, second))
        if key in seen:
            continue
        seen.add(key)
        adjacency.append({"segment_id": key[0], "faces": [key[1], key[2]]})
    return {
        "faces": superfaces,
        "face_adjacency": adjacency,
        "profile": dict(profile),
        "forbidden_segment_ids": sorted(forbidden),
        "forbidden_segment_reasons": reasons,
        "summary": {
            "base_face_count": len(faces),
            "superface_count": len(superfaces),
            "invalid_superface_count": len(invalid),
            "dissolved_separator_count": len(dissolved),
            "forbidden_graph_segment_count": len(forbidden),
        },
    }

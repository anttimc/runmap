"""Reusable pedestrian access, surface, and stair profile filtering."""


def _tag(segment, name):
    value = segment.get("tags", {}).get(name)
    return str(value).strip().lower() if value is not None else ""


def effective_edge_profile(profile=None, stairs=None):
    """Combine an edge profile with the independent stair policy."""
    stairs = dict(stairs or {})
    mode = stairs.get("mode", "avoid")
    if mode not in {"avoid", "penalize", "allow"}:
        raise ValueError("stairs.mode must be avoid, penalize, or allow")
    effective = dict(profile) if profile else None
    if mode == "avoid":
        effective = dict(effective or {"id": "default"})
        effective["forbidden_highways"] = sorted(
            set(effective.get("forbidden_highways", ())) | {"steps"}
        )
    return effective


def forbidden_reason(segment, profile):
    """Return a profile rejection reason, or None when the edge is usable."""
    access_reason = pedestrian_access_reason(segment.get("tags", {}), profile)
    if access_reason:
        return access_reason
    if _tag(segment, "indoor") not in {"", "no", "false", "0"}:
        return "indoor"
    highway = _tag(segment, "highway")
    surface = _tag(segment, "surface")
    if highway in set(profile.get("forbidden_highways", ())):
        return f"highway:{highway}"
    if surface in set(profile.get("forbidden_surfaces", ())):
        return f"surface:{surface}"
    if not surface and highway in set(profile.get("forbid_unknown_surface_highways", ())):
        return f"unknown_surface:{highway}"
    return None


def pedestrian_access_reason(tags, profile):
    """Apply pedestrian restrictions before generic access and its exceptions."""
    denied = set(profile.get("forbidden_access_values", ("no", "private")))
    tagged = {"tags": tags}
    foot = _tag(tagged, "foot")
    access = _tag(tagged, "access")
    if foot in denied:
        return f"foot:{foot}"
    if access in denied and foot not in {"yes", "designated", "permissive", "official"}:
        return f"access:{access}"
    return None


def classify_segments(index, profile):
    restricted_nodes = {
        node_id: reason for node_id, node in index.nodes.items()
        if (reason := pedestrian_access_reason(node, profile))
    }
    reasons = {}
    for segment_id, segment in index.segments.items():
        reason = forbidden_reason(segment, profile)
        if not reason:
            for node_id in (segment["from"], segment["to"]):
                if node_id in restricted_nodes:
                    reason = f"node:{node_id}:{restricted_nodes[node_id]}"
                    break
        if reason:
            reasons[segment_id] = reason
    return reasons

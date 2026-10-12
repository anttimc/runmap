"""Reusable pedestrian access, surface, and stair profile filtering."""


PUBLIC_ACCESS_DENIED = ("no", "private", "destination", "customers", "permit", "delivery")


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
    profile = profile or exploration_profile(avoid_stairs=False)
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
    if profile.get("surface_preference") == "surfaced" and surface_category(segment) not in {"paved", "gravel"}:
        return "surface:unknown" if not surface else f"surface:{surface}"
    if highway == "cycleway" and _tag(segment, "foot") not in {"yes", "designated", "permissive", "official"}:
        return "cycleway_without_foot_permission"
    if not any(pedestrian_directions(segment.get("tags", {}), profile)):
        return "pedestrian_directions"
    return None


def pedestrian_access_reason(tags, profile):
    """Apply pedestrian restrictions before generic access and its exceptions."""
    profile = profile or exploration_profile(avoid_stairs=False)
    denied = set(profile.get("forbidden_access_values", PUBLIC_ACCESS_DENIED))
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


def surface_category(segment):
    """Classify tagged material without inferring maintenance from highway type."""
    surface = _tag(segment, "surface")
    if not surface:
        return "unknown"
    if surface in {"asphalt", "concrete", "concrete:plates", "concrete:lanes", "paved",
                   "paving_stones", "sett", "cobblestone", "unhewn_cobblestone"}:
        return "paved"
    if surface in {"compacted", "fine_gravel", "gravel", "pebblestone"}:
        return "gravel"
    if surface in {"dirt", "earth", "ground", "grass", "mud", "sand", "rock"}:
        return "natural"
    return "other"


def exploration_profile(surface="all", avoid_stairs=True):
    """Normalize public pedestrian eligibility independently of terrain and costs."""
    if surface not in {"all", "surfaced"}:
        raise ValueError("Unknown surface filter")
    return {"id": f"public:{surface}:stairs-{int(avoid_stairs)}",
            "forbidden_access_values": list(PUBLIC_ACCESS_DENIED),
            "forbidden_highways": ["steps"] if avoid_stairs else [],
            "surface_preference": surface}


def pedestrian_directions(tags, profile=None):
    profile = profile or exploration_profile(avoid_stairs=False)
    denied = set(profile.get("forbidden_access_values", PUBLIC_ACCESS_DENIED))
    normalized = {name: str(value).strip().lower() for name, value in tags.items()}
    forward = normalized.get("foot:forward", "") not in denied
    backward = normalized.get("foot:backward", "") not in denied
    oneway = normalized.get("oneway:foot", "")
    if oneway in {"yes", "true", "1"}:
        backward = False
    elif oneway == "-1":
        forward = False
    return forward, backward


def arc_forward(segment, arc):
    """Identify traversal direction, including self-loops and temporary partial arcs."""
    if arc["id"].endswith(":f"):
        return True
    if arc["id"].endswith(":b"):
        return False
    return arc["from"] == segment["from"] and arc["to"] != segment["from"]


def arc_direction_allowed(segment, arc, profile):
    forward, backward = pedestrian_directions(segment.get("tags", {}), profile)
    return (forward and segment.get("forward", True)) if arc_forward(segment, arc) else (backward and segment.get("backward", True))

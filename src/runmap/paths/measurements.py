"""File-backed terrain measurements and direction-independent inspection."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from rasterio.warp import transform


def graph_digest(graph):
    """Hash the published graph, excluding its measurement reference."""
    document = {key: value for key, value in graph.items()
                if key not in {"_measurements", "measurements"}}
    return hashlib.sha256(json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


@dataclass(frozen=True)
class MeasurementData:
    """Measurement document and graph lengths indexed by physical segment ID."""

    document: dict
    graph_lengths_m: dict


def validate_measurements(graph, document):
    """Reject unsupported measurements or a mismatched graph identity."""
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported measurement schema")
    if document.get("graph_digest") != graph_digest(graph):
        raise ValueError("Graph/measurement identity mismatch")
    if document["segments"].keys() != {segment["id"] for segment in graph["segments"]}:
        raise ValueError("Graph/measurement segment mismatch")


def build_measurement_data(graph, document):
    """Validate measurements and collect the graph data needed for inspection."""
    validate_measurements(graph, document)
    return MeasurementData(
        document=document,
        graph_lengths_m={segment["id"]: segment["length_m"] for segment in graph["segments"]},
    )


def load_measurements(graph_path):
    """Read a published graph/measurement pair and verify its checksum and identity."""
    graph_path = Path(graph_path)
    graph = json.loads(graph_path.read_text())
    reference = graph["measurements"]
    path = graph_path.parent / reference["file"]
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != reference["sha256"]:
        raise ValueError("Measurement checksum mismatch")
    return build_measurement_data(graph, json.loads(payload))


def inspect_measurements(data, segment_id, reverse=False, start_m=0.0, end_m=None):
    """Slice in projected chainage; reverse the resulting inspection if requested.

    Gaps have null profile heights. Each following valid run starts at zero.
    Partial interval height changes use the interval's constant sampled grade.
    """
    source = data.document["segments"][segment_id]
    length = source[-1]["end_m"] if source else 0.0
    end_m = length if end_m is None else end_m
    if not 0 <= start_m < end_m <= length:
        raise ValueError("Invalid inspection chainage range")
    intervals = []
    for item in source:
        lo, hi = max(start_m, item["start_m"]), min(end_m, item["end_m"])
        if hi <= lo:
            continue
        span = item["end_m"] - item["start_m"]
        a, b = (lo - item["start_m"]) / span, (hi - item["start_m"]) / span
        # Slice in the same projected coordinate system used for sampling.
        lon, lat = zip(*item["geometry"])
        x, y = transform("EPSG:4326", "EPSG:3067", lon, lat)
        sx = [x[0] + f * (x[1] - x[0]) for f in (a, b)]
        sy = [y[0] + f * (y[1] - y[0]) for f in (a, b)]
        lon, lat = transform("EPSG:3067", "EPSG:4326", sx, sy)
        graph_span = item["graph_end_m"] - item["graph_start_m"]
        intervals.append({**item, "start_m": lo - start_m, "end_m": hi - start_m,
                          "graph_start_m": item["graph_start_m"] + a * graph_span,
                          "graph_end_m": item["graph_start_m"] + b * graph_span,
                          "geometry": [list(p) for p in zip(lon, lat)],
                          "height_change_m": None if not item["valid"] else
                          item["height_change_m"] * (hi - lo) / span})
    if reverse:
        graph_length = data.graph_lengths_m[segment_id]
        intervals.reverse()
        for item in intervals:
            item["start_m"], item["end_m"] = end_m - start_m - item["end_m"], end_m - start_m - item["start_m"]
            item["graph_start_m"], item["graph_end_m"] = graph_length - item["graph_end_m"], graph_length - item["graph_start_m"]
            item["geometry"].reverse()
            if item["valid"]:
                item["grade_percent"] *= -1
                item["height_change_m"] *= -1
    height = 0.0
    run = -1
    previous_valid = False
    for item in intervals:
        if not item["valid"]:
            item.update(profile_run=None, relative_start_m=None, relative_end_m=None)
            previous_valid = False
            continue
        if not previous_valid:
            height = 0.0
            run += 1
        item.update(profile_run=run, relative_start_m=height)
        height += item["height_change_m"]
        item["relative_end_m"] = height
        previous_valid = True
    valid_length = sum(i["end_m"] - i["start_m"] for i in intervals if i["valid"])
    return {"segment_id": segment_id, "reverse": reverse, "intervals": intervals,
            "profile_label": "Estimated terrain height change; resets to zero after gaps",
            "ascent_m": sum(max(0, i["height_change_m"]) for i in intervals if i["valid"]),
            "descent_m": sum(max(0, -i["height_change_m"]) for i in intervals if i["valid"]),
            "valid_length_fraction": valid_length / (end_m - start_m),
            "totals_complete": all(i["valid"] for i in intervals),
            "method": data.document["method"]}

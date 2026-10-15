"""Inspect preference evidence without downloading data or rebuilding the graph."""

import argparse
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import TypedDict, cast

from runmap.paths.graph import OsmDocument, OsmElement
from runmap.paths.measurements import validate_measurements
from runmap.routing.types import GraphDocument, Measurement, MeasurementDocument, Segment


class MeasurementReference(TypedDict):
    file: str
    sha256: str
    size_bytes: int


class AuditGraph(GraphDocument):
    measurements: MeasurementReference


@dataclass(frozen=True)
class AuditData:
    graph: AuditGraph
    measurements: MeasurementDocument
    nodes: dict[str, OsmElement]
    ways: dict[str, OsmElement]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_data(graph_path: Path) -> tuple[AuditData, Path]:
    graph = cast(AuditGraph, json.loads(graph_path.read_text()))
    source = graph["source"]["osm"]
    source_path = Path(source["source_path"])
    if _sha256(source_path) != source["sha256"]:
        raise ValueError("Source checksum differs from prepared graph provenance")
    raw = cast(OsmDocument, json.loads(source_path.read_text()))
    reference = graph["measurements"]
    path = graph_path.parent / reference["file"]
    if _sha256(path) != reference["sha256"]:
        raise ValueError("Measurement checksum mismatch")
    measurements = cast(MeasurementDocument, json.loads(path.read_text()))
    validate_measurements(graph, measurements)
    elements = raw["elements"]
    return AuditData(graph, measurements,
                     {str(e["id"]): e for e in elements if e["type"] == "node"},
                     {str(e["id"]): e for e in elements if e["type"] == "way"}), source_path


def _tag_values(elements: Sequence[OsmElement], key: str) -> dict[str, int]:
    return dict(sorted(Counter(e["tags"][key] for e in elements
                               if key in e.get("tags", {})).items()))


def _retention(data: AuditData) -> dict[str, object]:
    node_index = {n["id"]: n for n in data.graph["nodes"]}
    way_ids = {s["osm_way_id"] for s in data.graph["segments"]}
    keys = ("highway", "crossing", "crossing:signals", "crossing:island",
            "crossing:markings", "footway", "cycleway", "traffic_signals:direction",
            "maxspeed", "lanes", "bridge", "tunnel", "layer", "incline", "railway")
    rows: dict[str, object] = {}
    for key in keys:
        source_nodes = {nid for nid, n in data.nodes.items() if key in n.get("tags", {})}
        source_ways = {wid for wid, w in data.ways.items() if key in w.get("tags", {})}
        rows[key] = {
            "source_node_values": _tag_values(list(data.nodes.values()), key),
            "source_way_values": _tag_values(list(data.ways.values()), key),
            "source_nodes_in_graph": len(source_nodes & node_index.keys()),
            "prepared_nodes_retaining_tag": sum(key in node_index[nid]
                                                 for nid in source_nodes & node_index.keys()),
            "source_ways_in_graph": len(source_ways & way_ids),
            "prepared_ways_retaining_tag": len({s["osm_way_id"] for s in data.graph["segments"]
                                                 if key in s["tags"]}),
        }
    return rows


def _terrain(data: AuditData) -> dict[str, object]:
    intervals = [i for items in data.measurements["segments"].values() for i in items]
    total = sum(i["end_m"] - i["start_m"] for i in intervals)
    valid = sum(i["end_m"] - i["start_m"] for i in intervals if i["valid"])
    flags = Counter(flag for i in intervals for flag in i["quality_flags"])
    flagged_lengths = {flag: sum(i["end_m"] - i["start_m"] for i in intervals
                                if flag in i["quality_flags"]) for flag in sorted(flags)}
    uncertain = sum(i["end_m"] - i["start_m"] for i in intervals
                    if {"bridge", "tunnel", "missing_terrain"} & set(i["quality_flags"]))
    grades = [i["grade_percent"] for i in intervals if i["grade_percent"] is not None]
    return {"method": data.measurements["method"], "interval_count": len(intervals),
            "sampled_length_m": total, "valid_length_m": valid,
            "valid_length_fraction": valid / total if total else None,
            "uncertain_bridge_tunnel_or_missing_length_m": uncertain,
            "quality_flag_intervals": dict(sorted(flags.items())),
            "quality_flag_length_m": flagged_lengths,
            "intervals_above_20_percent_absolute_grade": sum(abs(g) > 20 for g in grades),
            "minimum_signed_grade_percent": min(grades, default=None),
            "maximum_signed_grade_percent": max(grades, default=None)}


def _signal(tags: Mapping[str, object]) -> bool:
    return tags.get("crossing") == "traffic_signals" or tags.get("crossing:signals") == "yes"


def _signals(data: AuditData) -> dict[str, object]:
    graph_nodes = {n["id"]: n for n in data.graph["nodes"]}
    signals = {nid: n for nid, n in data.nodes.items() if _signal(n.get("tags", {}))}
    retained = {nid for nid, n in graph_nodes.items() if _signal(dict(n))}
    adjacent = [s["id"] for s in data.graph["segments"]
                if s["from"] in signals and s["to"] in signals]
    return {"explicit_crossing_signal_source_nodes": len(signals),
            "explicit_crossing_signal_nodes_in_graph": len(signals.keys() & graph_nodes.keys()),
            "explicit_crossing_signal_nodes_with_railway_tag": sum("railway" in n.get("tags", {})
                                                                  for n in signals.values()),
            "recognizable_signal_nodes_after_preparation": len(retained),
            "lost_signal_evidence_node_ids": sorted((signals.keys() & graph_nodes.keys()) - retained),
            "signal_to_signal_segment_count": len(adjacent),
            "signal_to_signal_segment_examples": sorted(adjacent)[:10],
            "highway_traffic_signals_source_nodes": sum(n.get("tags", {}).get("highway") == "traffic_signals"
                                                        for n in data.nodes.values()),
            "event_groups_prepared": False}


def _segment_fixture(data: AuditData, label: str, segment: Segment) -> dict[str, object]:
    items = data.measurements["segments"][segment["id"]]
    return {"id": label, "review_status": "source-derived; local runner review pending",
            "segment": segment, "source_way": data.ways[segment["osm_way_id"]],
            "interval_count": len(items),
            "grade_range_percent": [min((i["grade_percent"] for i in items if i["valid"]), default=None),
                                    max((i["grade_percent"] for i in items if i["valid"]), default=None)],
            "quality_flags": sorted({flag for i in items for flag in i["quality_flags"]})}


def _near_home(segment: Segment) -> float:
    lon, lat = segment["geometry"][0]
    return ((lon - 24.80259) * 0.5) ** 2 + (lat - 60.21682) ** 2


def _terrain_fixtures(data: AuditData) -> list[dict[str, object]]:
    def clean(items: list[Measurement]) -> bool:
        return bool(items) and all(i["valid"] and not i["quality_flags"] for i in items)

    def grades(items: list[Measurement]) -> list[float]:
        return [i["grade_percent"] for i in items if i["grade_percent"] is not None]

    def height_change(items: list[Measurement]) -> float:
        return sum(i["height_change_m"] for i in items if i["height_change_m"] is not None)

    rules: list[tuple[str, Callable[[Segment, list[Measurement]], bool]]] = [
        ("flat-corridor", lambda s, items: s["length_m"] >= 50 and clean(items)
         and all(abs(g) <= 2 for g in grades(items))),
        ("directional-hill", lambda s, items: s["length_m"] >= 50 and clean(items)
         and abs(height_change(items)) >= 3),
        ("rolling-path", lambda s, items: s["length_m"] >= 50 and clean(items)
         and s["ascent_m"] >= 2 and s["descent_m"] >= 2),
        ("bridge-ground-estimate", lambda s, items: any("bridge" in i["quality_flags"] for i in items)),
        ("underpass-ground-estimate", lambda s, items: any("tunnel" in i["quality_flags"] for i in items)),
        ("stairs", lambda s, items: s["tags"].get("highway") == "steps"),
        ("missing-surface", lambda s, items: "surface" not in s["tags"]),
    ]
    fixtures = []
    for label, predicate in rules:
        candidates = [s for s in data.graph["segments"]
                      if predicate(s, data.measurements["segments"][s["id"]])]
        if candidates:
            fixtures.append(_segment_fixture(data, label, min(candidates, key=_near_home)))
    extreme = max(data.graph["segments"], key=lambda s: max(
        (abs(g) for g in grades(data.measurements["segments"][s["id"]])), default=0))
    fixtures.append(_segment_fixture(data, "extreme-grade-review", extreme))
    return fixtures


def _crossing_fixtures(data: AuditData) -> list[dict[str, object]]:
    incident: dict[str, list[Segment]] = defaultdict(list)
    for segment in data.graph["segments"]:
        for node_id in (segment["from"], segment["to"]):
            incident[node_id].append(segment)
    graph_nodes = {n["id"]: n for n in data.graph["nodes"]}
    fixtures = []
    # Fixed local examples retain source identities across graph preparations.
    for label, node_ids in (
        ("multi-node-crossing-near-school", ["1137173762", "1137173716"]),
        ("crossing-with-road-and-path-branches", ["38461548"]),
    ):
        if not all(nid in data.nodes for nid in node_ids):
            continue
        fixtures.append({"id": label, "review_status": "event attribution pending",
                         "source_nodes": [data.nodes[nid] for nid in node_ids],
                         "prepared_nodes": [graph_nodes.get(nid) for nid in node_ids],
                         "incident_segments": list({s["id"]: s for nid in node_ids
                                                    for s in incident[nid]}.values())})
    for nid, node in sorted(data.nodes.items()):
        tags = node.get("tags", {})
        if tags.get("crossing:signals") == "yes" and tags.get("crossing") != "traffic_signals":
            fixtures.append({"id": f"modern-signal-tag-{nid}", "source_nodes": [node],
                             "prepared_nodes": [graph_nodes.get(nid)],
                             "incident_segments": incident[nid]})
    return fixtures


def audit(graph_path: Path) -> dict[str, object]:
    data, source_path = _load_data(graph_path)
    provenance_path = source_path.with_suffix(source_path.suffix + ".json")
    raw = cast(dict[str, object], json.loads(source_path.read_text()))
    elements = list(data.nodes.values()) + list(data.ways.values())
    return {
        "audit_schema_version": 1,
        "graph_path": str(graph_path), "graph_sha256": _sha256(graph_path),
        "graph_digest": data.measurements["graph_digest"],
        "graph_schema_version": data.graph["schema_version"],
        "preparation_code_sha256_at_audit": {str(path): _sha256(path) for path in (
            Path("src/runmap/sources/osm.py"), Path("src/runmap/paths/graph.py"),
            Path("src/runmap/paths/terrain.py"))},
        "preparation_code_note": "Current code identity; graph does not record the preparing Git revision.",
        "source": data.graph["source"],
        "source_provenance": json.loads(provenance_path.read_text()),
        "source_snapshot": raw.get("osm3s"),
        "source_element_counts": dict(Counter(e["type"] for e in cast(list[OsmElement], raw["elements"]))),
        "measurements": data.graph["measurements"], "graph_summary": data.graph["summary"],
        "attribute_retention": _retention(data), "terrain": _terrain(data),
        "signals": _signals(data),
        "context": {"source_highway_way_counts": _tag_values(list(data.ways.values()), "highway"),
                    "incidental_context_tag_values": {key: _tag_values(elements, key)
                                                      for key in ("natural", "landuse", "leisure")},
                    "complete_major_road_context": False, "complete_green_context": False,
                    "context_buffer_m": None,
                    "coverage_note": "Highway-only query plus ref 101; incidental tags do not establish context coverage."},
        "fixtures": _terrain_fixtures(data) + _crossing_fixtures(data),
        "fixture_gaps": ["missing terrain (none in current sample; synthetic fixture required)",
                         "avoidable/unavoidable signal routes and endpoint passage semantics",
                         "road crossing versus parallel exposure with complete road context",
                         "park, wood, private green land and green/road conflict with polygon context",
                         "buffer boundary coverage"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(audit(args.graph), indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()

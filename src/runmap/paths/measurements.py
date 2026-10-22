"""File-backed terrain measurements and direction-independent inspection."""

from dataclasses import dataclass, replace
from collections.abc import Iterator, Mapping, Sequence
import hashlib
import json
from itertools import islice
from pathlib import Path

from rasterio.warp import transform
import numpy as np
from numpy.typing import NDArray
from runmap.routing.integer_graph import IntegerGraph
from runmap.routing.serialization import prepared_json
from runmap.routing.types import Coordinate, GraphDocument, Measurement, MeasurementDocument, TerrainMetadata


def graph_digest(graph: GraphDocument) -> str:
    """Hash the published graph, excluding its measurement reference."""
    document = {key: value for key, value in graph.items()
                if key not in {"_measurements", "measurements"}}
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False, default=prepared_json)
    digest = hashlib.sha256(b"{")
    for number, key in enumerate(sorted(document)):
        digest.update(("," if number else "").encode())
        digest.update(encoder.encode(key).encode())
        digest.update(b":")
        if key in {"nodes", "segments", "arcs"}:
            for payload in _record_digest_chunks(graph[key], encoder):
                digest.update(payload)
        else:
            digest.update(encoder.encode(document[key]).encode())
    digest.update(b"}")
    return digest.hexdigest()


def _record_digest_chunks(items: Sequence[object], encoder: json.JSONEncoder) -> Iterator[bytes]:
    """Bound hashing's temporary materialization of packed public records."""
    yield b"["
    records = iter(items)
    first = True
    while batch := list(islice(records, 1024)):
        yield ("" if first else ",").encode()
        yield encoder.encode(batch)[1:-1].encode()
        first = False
    yield b"]"


@dataclass(frozen=True)
class MeasurementData:
    """Packed samples in the prepared graph's physical-segment order."""

    graph: GraphDocument
    graph_digest: str
    method: TerrainMetadata
    segment_ids: tuple[str, ...]
    segment_indices: Mapping[str, int]
    graph_lengths_m: NDArray[np.float64]
    interval_offsets: NDArray[np.int64]
    values: NDArray[np.float64]  # start/end, graph start/end, grade, height change
    present: NDArray[np.bool_]  # independent grade and height nullability
    valid: NDArray[np.bool_]
    geometry: NDArray[np.float64]
    geometry_offsets: NDArray[np.int64]
    flag_codes: NDArray[np.uint32]
    flag_values: tuple[tuple[str, ...], ...]


def validate_measurements(graph: GraphDocument, document: MeasurementDocument) -> None:
    """Reject unsupported measurements or a mismatched graph identity."""
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported measurement schema")
    if document.get("graph_digest") != graph_digest(graph):
        raise ValueError("Graph/measurement identity mismatch")
    if document["segments"].keys() != {segment["id"] for segment in graph["segments"]}:
        raise ValueError("Graph/measurement segment mismatch")


def build_measurement_data(graph: GraphDocument, document: MeasurementDocument,
                           *, index: IntegerGraph | None = None) -> MeasurementData:
    """Discard per-sample dictionaries after packing lossless numeric columns."""
    validate_measurements(graph, document)
    ids = tuple(segment["id"] for segment in graph["segments"]) if index is None else index.segment_ids
    if set(ids) != document["segments"].keys() or (index is not None and (
            index.extra_node_ids or index.extra_segment_ids or index.extra_arcs or index.arc_overrides)):
        raise ValueError("Measurements require the base graph segment ordering")
    indices = {key: i for i, key in enumerate(ids)} if index is None else index.segment_indices
    data = _allocate_measurements(graph, document, ids, indices)
    flags: dict[tuple[str, ...], int] = {}
    for segment, key in enumerate(ids):
        for local, item in enumerate(document["segments"][key]):
            if item["segment_id"] != key or item["interval_index"] != local:
                raise ValueError("Measurement interval identity mismatch")
            _pack_interval(data, int(data.interval_offsets[segment]) + local, item, flags)
    for array in (data.graph_lengths_m, data.interval_offsets, data.values, data.present, data.valid,
                  data.geometry, data.geometry_offsets, data.flag_codes):
        array.flags.writeable = False
    return replace(data, flag_values=tuple(flags))


def _allocate_measurements(graph: GraphDocument, document: MeasurementDocument,
                           ids: tuple[str, ...], indices: Mapping[str, int]) -> MeasurementData:
    offsets = np.zeros(len(ids) + 1, dtype=np.int64)
    for i, key in enumerate(ids):
        offsets[i + 1] = offsets[i] + len(document["segments"][key])
    count = int(offsets[-1])
    geometry_count = sum(len(item["geometry"]) for items in document["segments"].values() for item in items)
    lengths = {segment["id"]: segment["length_m"] for segment in graph["segments"]}
    return MeasurementData(
        graph, document["graph_digest"], document["method"].copy(), ids, indices,
        np.array([lengths[key] for key in ids], dtype=np.float64), offsets,
        np.empty((count, 6), dtype=np.float64), np.empty((count, 2), dtype=np.bool_),
        np.empty(count, dtype=np.bool_), np.empty((geometry_count, 2), dtype=np.float64),
        np.zeros(count + 1, dtype=np.int64), np.empty(count, dtype=np.uint32), ())


def _pack_interval(data: MeasurementData, number: int, item: Measurement,
                   flags: dict[tuple[str, ...], int]) -> None:
    grade, height = item["grade_percent"], item["height_change_m"]
    data.values[number] = (item["start_m"], item["end_m"], item["graph_start_m"],
                           item["graph_end_m"], 0.0 if grade is None else grade,
                           0.0 if height is None else height)
    data.present[number] = grade is not None, height is not None
    data.valid[number] = item["valid"]
    lo = int(data.geometry_offsets[number])
    hi = lo + len(item["geometry"])
    data.geometry[lo:hi] = item["geometry"]
    data.geometry_offsets[number + 1] = hi
    flag_tuple = tuple(item["quality_flags"])
    data.flag_codes[number] = flags.setdefault(flag_tuple, len(flags))


def indexed_measurement(data: MeasurementData, segment: int, interval: int) -> Measurement:
    """Materialize one API record without retaining a second representation."""
    if not 0 <= segment < len(data.segment_ids):
        raise IndexError("Measurement segment index out of range")
    lo, hi = int(data.interval_offsets[segment]), int(data.interval_offsets[segment + 1])
    if not 0 <= interval < hi - lo:
        raise IndexError("Measurement interval index out of range")
    number = lo + interval
    values = data.values[number]
    return Measurement(
        segment_id=data.segment_ids[segment], interval_index=interval,
        start_m=float(values[0]), end_m=float(values[1]), graph_start_m=float(values[2]),
        graph_end_m=float(values[3]), geometry=measurement_geometry(data, number),
        grade_percent=float(values[4]) if data.present[number, 0] else None,
        height_change_m=float(values[5]) if data.present[number, 1] else None,
        valid=bool(data.valid[number]), quality_flags=list(data.flag_values[int(data.flag_codes[number])]))


def measurement_geometry(data: MeasurementData, number: int) -> list[Coordinate]:
    a, b = int(data.geometry_offsets[number]), int(data.geometry_offsets[number + 1])
    return data.geometry[a:b].tolist()


def segment_measurements(data: MeasurementData, segment_id: str) -> list[Measurement]:
    segment = data.segment_indices[segment_id]
    count = int(data.interval_offsets[segment + 1] - data.interval_offsets[segment])
    return [indexed_measurement(data, segment, i) for i in range(count)]


def load_measurements(graph_path: str | Path, *, graph: GraphDocument | None = None,
                      index: IntegerGraph | None = None) -> MeasurementData:
    """Read a published graph/measurement pair and verify its checksum and identity."""
    graph_path = Path(graph_path)
    if graph is None:
        graph = json.loads(graph_path.read_text())
    reference = graph["measurements"]
    path = graph_path.parent / reference["file"]
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != reference["sha256"]:
        raise ValueError("Measurement checksum mismatch")
    document = json.loads(payload)
    del payload
    return build_measurement_data(graph, document, index=index)


def inspect_measurements(data: MeasurementData, segment_id: str, reverse: bool = False,
                         start_m: float = 0.0, end_m: float | None = None) -> dict:
    """Slice in projected chainage; reverse the resulting inspection if requested.

    Gaps have null profile heights. Each following valid run starts at zero.
    Partial interval height changes use the interval's constant sampled grade.
    """
    source = segment_measurements(data, segment_id)
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
        graph_length = float(data.graph_lengths_m[data.segment_indices[segment_id]])
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
            "method": data.method}

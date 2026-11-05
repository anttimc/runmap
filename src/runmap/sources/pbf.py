"""Derive environment geometry and reference-complete regional highways from one OSM PBF."""

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import osmium
from rasterio.warp import transform_bounds, transform_geom
from shapely import from_wkb
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

from runmap.paths.graph import OsmElement
from runmap.sources.environment import EnvironmentRequest, file_identity, load_request, query_bounds, _write_json


@dataclass
class ImportData:
    features: list[dict[str, object]]
    ways: list[OsmElement]
    node_ids: set[int]
    counts: Counter[str]
    area_relations: set[int]
    assembled_relations: set[int]


def selected_tags(tags: dict[str, str], request: EnvironmentRequest) -> bool:
    return any(key in tags and (values is True or tags[key] in values)
               for key, values in request.tags.items())


def intersects_region(geometry: BaseGeometry, envelope: BaseGeometry, metric: BaseGeometry) -> bool:
    if geometry.is_empty or not box(*geometry.bounds).intersects(envelope):
        return False
    projected = shape(transform_geom("EPSG:4326", "EPSG:3067", mapping(geometry)))
    return projected.intersects(metric)


def retain_geometry(data: ImportData, geometry: BaseGeometry, tags: dict[str, str],
                    identifier: int, kind: str, envelope: BaseGeometry, metric: BaseGeometry) -> None:
    if not geometry.is_valid:
        data.counts["invalid_geometries_in_source"] += 1
        return
    if intersects_region(geometry, envelope, metric):
        data.features.append({"type": "Feature", "properties": {**tags, "osm_type": kind, "osm_id": identifier},
                              "geometry": mapping(geometry)})


def process_way(obj: osmium.osm.Way, data: ImportData, request: EnvironmentRequest,
                factory: osmium.geom.WKBFactory, envelope: BaseGeometry,
                metric: BaseGeometry, core: BaseGeometry, core_envelope: BaseGeometry) -> None:
    tags = dict(obj.tags)
    environmental = selected_tags(tags, request)
    highway = "highway" in tags
    if not environmental and not highway:
        return
    try:
        geometry = from_wkb(factory.create_linestring(obj))
    except (RuntimeError, osmium.InvalidLocationError):
        data.counts["invalid_way_geometry_in_source"] += 1
        return
    if highway and intersects_region(geometry, core_envelope, core):
        references = [node.ref for node in obj.nodes]
        data.ways.append({"type": "way", "id": obj.id, "nodes": references, "tags": tags})
        data.node_ids.update(references)
    # Closed polygon evidence is emitted by area assembly, with holes preserved.
    if environmental and (highway or tags.get("natural") == "coastline" or not obj.is_closed()):
        retain_geometry(data, geometry, tags, obj.id, "way", envelope, metric)


def process_area(obj: osmium.osm.Area, data: ImportData, request: EnvironmentRequest,
                 factory: osmium.geom.WKBFactory, envelope: BaseGeometry, metric: BaseGeometry) -> None:
    if not obj.from_way():
        data.assembled_relations.add(obj.orig_id())
    tags = dict(obj.tags)
    if not selected_tags(tags, request):
        return
    try:
        geometry = from_wkb(factory.create_multipolygon(obj))
    except (RuntimeError, osmium.InvalidLocationError):
        data.counts["invalid_area_geometry_in_source"] += 1
        return
    retain_geometry(data, geometry, tags, obj.orig_id(), "way" if obj.from_way() else "relation", envelope, metric)


def collect_objects(source: Path, request: EnvironmentRequest, temporary: Path) -> ImportData:
    west, south, east, north = request.bounds_epsg3067
    halo = request.halo_m
    metric = box(west - halo, south - halo, east + halo, north + halo)
    envelope = box(*query_bounds(request))
    core = box(west, south, east, north)
    core_envelope = box(*transform_bounds("EPSG:3067", "EPSG:4326", west, south, east, north, densify_pts=21))
    data = ImportData([], [], set(), Counter(), set(), set())
    factory = osmium.geom.WKBFactory()
    keys = tuple(set(request.tags) | {"highway"})
    processor = (osmium.FileProcessor(source)
                 .with_locations(f"sparse_file_array,{temporary / 'node-locations'}")
                 .with_areas(osmium.filter.KeyFilter(*request.tags))
                 .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY | osmium.osm.AREA | osmium.osm.RELATION))
                 .with_filter(osmium.filter.KeyFilter(*keys)))
    for obj in processor:
        if isinstance(obj, osmium.osm.Way):
            process_way(obj, data, request, factory, envelope, metric, core, core_envelope)
        elif isinstance(obj, osmium.osm.Area):
            process_area(obj, data, request, factory, envelope, metric)
        elif isinstance(obj, osmium.osm.Relation) and obj.tags.get("type") == "multipolygon" and selected_tags(dict(obj.tags), request):
            data.area_relations.add(obj.id)
    del processor
    data.counts["unassembled_tagged_multipolygons_in_source"] = len(data.area_relations - data.assembled_relations)
    return data


def collect_nodes(source: Path, identifiers: set[int]) -> list[OsmElement]:
    """Read every referenced node, including tags and vertices outside the selection rectangle."""
    nodes: list[OsmElement] = []
    for node in (osmium.FileProcessor(source, osmium.osm.NODE)
                 .with_filter(osmium.filter.IdFilter(identifiers))):
        if not node.location.valid():
            raise ValueError(f"Missing location for required routing node {node.id}")
        nodes.append({"type": "node", "id": node.id, "lat": node.lat, "lon": node.lon, "tags": dict(node.tags)})
    if len(nodes) != len(identifiers):
        raise ValueError("PBF lacks nodes required by selected highway ways")
    return nodes


def source_metadata(source: Path) -> dict[str, object]:
    identity = file_identity(source)
    provider: dict[str, object] = {}
    manifest_path = source.with_name("manifest.json")
    if manifest_path.exists():
        provider = json.loads(manifest_path.read_text())
        if provider.get("pbf") != identity:
            raise ValueError("PBF does not match its snapshot manifest")
    with osmium.io.Reader(source) as reader:
        header = reader.header()
        timestamp = header.get("osmosis_replication_timestamp")
        bounds = header.box()
        if not bounds.valid():
            raise ValueError("PBF has no coverage bounds; cannot establish regional source support")
        coverage = [bounds.bottom_left.lon, bounds.bottom_left.lat, bounds.top_right.lon, bounds.top_right.lat]
    return {"pbf": identity, "snapshot": provider, "osm_base_timestamp": timestamp,
            "source_bounds_wgs84": coverage, "source_path": str(source.resolve())}


def extract_region(source: Path, config: Path, destination: Path) -> Path:
    started = perf_counter()
    request = load_request(config)
    if destination.exists():
        raise FileExistsError(f"Regional output already exists: {destination}")
    metadata = source_metadata(source)
    if not box(*metadata["source_bounds_wgs84"]).covers(box(*query_bounds(request))):
        raise ValueError("PBF header coverage does not enclose requested core and halo")
    destination.mkdir(parents=True)
    environment = destination / "environment"
    environment.mkdir()
    _write_json(environment / "request.json", asdict(request))
    print("Assembling full ways and multipolygons; node locations use a temporary disk index…", flush=True)
    with TemporaryDirectory(prefix="pbf-index-", dir=destination) as temporary:
        data = collect_objects(source, request, Path(temporary))
    print(f"Selected {len(data.features)} environment features and {len(data.ways)} highway ways; loading referenced nodes…", flush=True)
    nodes = collect_nodes(source, data.node_ids)
    _write_json(environment / "features.geojson", {"type": "FeatureCollection", "features": data.features})
    _write_json(destination / "network.json", {"elements": [*nodes, *data.ways]})
    implementation = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    shared = {"schema_version": 1, "provider": "OpenStreetMap PBF / local Pyosmium import", **metadata,
              "implementation_sha256": implementation,
              "dependencies": {name: version(name) for name in ("osmium", "shapely", "rasterio")},
              "licence": "ODbL 1.0", "licence_url": "https://www.openstreetmap.org/copyright"}
    features_identity = file_identity(environment / "features.geojson")
    _write_json(environment / "manifest.json", {**shared, "status": "regional-pbf-source",
                "request": asdict(request), "features": features_identity,
                "query_bbox_wgs84_left_bottom_right_top": query_bounds(request),
                "osm_base_timestamps": [metadata["osm_base_timestamp"]] if metadata["osm_base_timestamp"] else [],
                "feature_count": len(data.features), "geometry_diagnostics": dict(data.counts),
                "limitations": ["Source extract boundaries can omit relation members; assembly diagnostics cover the whole source.",
                                "Header bounds establish an envelope, not complete real-world mapping.",
                                "Only multipolygon relations are assembled; route and other relation semantics are not imported."]})
    network_identity = file_identity(destination / "network.json")
    _write_json(destination / "network.json.json", {**shared, **network_identity,
                "bounds_epsg3067": request.bounds_epsg3067, "node_count": len(nodes), "way_count": len(data.ways)})
    _write_json(destination / "manifest.json", {**shared, "request": asdict(request),
                "published_at": datetime.now(timezone.utc).isoformat(), "preparation_seconds": perf_counter() - started,
                "environment_features": features_identity, "network": network_identity,
                "geometry_diagnostics": dict(data.counts)})
    print(f"Published regional sources in {perf_counter() - started:.1f} s", flush=True)
    return destination / "manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(extract_region(args.source, args.config, args.output))


if __name__ == "__main__":
    main()

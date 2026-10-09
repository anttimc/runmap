"""Acquisition entry point: PYTHONPATH=src python3 -m runmap.cli."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError

from rasterio.warp import transform

from runmap.elevation.store import ElevationStore
from runmap.paths.experiments import find_experiment
from runmap.paths.graph import build_routing_graph, snap_to_vertex, write_graph_outputs
from runmap.paths.terrain import enrich_graph_terrain
from runmap.regions import PROCESSING_HALO_M, find_region, list_regions
from runmap.routing.baseline import generate_anchor_loops, write_route_outputs
from runmap.routing.atomic_regions import build_atomic_region_inventory, write_atomic_region_outputs
from runmap.routing.microcycles import build_microcycle_partition, write_microcycle_outputs
from runmap.routing.cycle_catalog import (
    generate_cycle_catalog,
    write_cycle_catalog_outputs,
)
from runmap.routing.face_search import generate_face_region_loops, write_face_route_outputs
from runmap.routing.search import GraphIndex
from runmap.routing.topology import prepare_topology, write_topology_outputs
from runmap.sources.mml import Bounds, coverage_url, download_elevation
from runmap.sources.osm import download_highways, highway_query


def local_key(path):
    """Read only MML_API_KEY from a plain local .env; never execute shell code."""
    if os.environ.get("MML_API_KEY"):
        return os.environ["MML_API_KEY"]
    if path.exists():
        for line in path.read_text().splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == "MML_API_KEY":
                return value.strip().strip("\"'")
    return ""


def legacy_download(argv=None):
    parser = argparse.ArgumentParser(description="Download a native 2 m MML elevation crop")
    parser.add_argument("--config", type=Path, default=Path("config/regions/leppavaara.json"))
    parser.add_argument("--output", type=Path, default=Path("data/raw/leppavaara/elevation-2m.tif"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--dry-run", action="store_true", help="Show the request without credentials or network access")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    if config.get("crs", "EPSG:3067") != "EPSG:3067":
        parser.error("This downloader expects EPSG:3067 bounds")
    bounds = Bounds(*config.get("bounds", config.get("bounds_epsg3067", [])))
    if args.dry_run:
        print(coverage_url(bounds))
        return
    key = local_key(args.env_file)
    if not key.strip():
        parser.error("Set MML_API_KEY in the environment or local .env; do not put it in command arguments")
    try:
        record = download_elevation(bounds, args.output, key)
    except HTTPError as exc:
        parser.exit(1, f"Download failed: HTTP {exc.code}. Check API key/service availability.\n")
    except URLError:
        parser.exit(1, "Download failed: network connection unavailable.\n")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Download failed: {exc}\n")
    print(f"Saved {args.output} ({record['bytes']} bytes); raster inspection is still required")


def region_command(argv):
    parser = argparse.ArgumentParser(description="Manage named Runmap elevation regions")
    parser.add_argument("--regions", type=Path, default=Path("config/regions"))
    parser.add_argument("--data-root", type=Path, default=Path("data/elevation"))
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List validated region definitions")
    prepare = subparsers.add_parser("prepare", help="Download and validate missing region tiles")
    prepare.add_argument("region_id")
    prepare.add_argument("--batch-id")
    prepare.add_argument("--dry-run", action="store_true")
    precompute = subparsers.add_parser("precompute", help="Build derived analysis tiles")
    precompute.add_argument("region_id")
    precompute.add_argument("--wavelength-m", type=int, default=40)
    args = parser.parse_args(argv)
    if args.command == "list":
        for region in list_regions(args.regions):
            print(f"{region.id}\t{region.title}\t{region.width_m:g} x {region.height_m:g} m")
        return
    region = find_region(args.regions, args.region_id)
    store = ElevationStore(args.data_root, args.catalog)
    if args.command == "precompute":
        paths = store.precompute_region(region, args.wavelength_m)
        print(
            f"Precomputed {len(paths)} analysis tiles for {region.id} "
            f"at {args.wavelength_m} m"
        )
        return
    if args.dry_run:
        print(f"Region {region.id}: {region.bounds} ({region.width_m:g} x {region.height_m:g} m)")
        for tile in region.source_tiles(PROCESSING_HALO_M):
            print(f"{tile.id}\t{tile.bounds}\t{coverage_url(Bounds(*tile.bounds))}")
        return
    key = local_key(args.env_file)
    try:
        manifest = store.prepare_region(region, key, batch_id=args.batch_id)
    except HTTPError as exc:
        parser.exit(1, f"Download failed: HTTP {exc.code}. Check API key/service availability.\n")
    except URLError:
        parser.exit(1, "Download failed: network connection unavailable.\n")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Region preparation failed: {exc}\n")
    print(
        f"Prepared {region.id}: {len(manifest['tiles'])} source tiles; "
        f"active manifest {manifest['id']}"
    )


def _wgs84_bounds(bounds_epsg3067):
    west, south, east, north = bounds_epsg3067
    xs = [west, west, east, east]
    ys = [south, north, south, north]
    longitudes, latitudes = transform("EPSG:3067", "EPSG:4326", xs, ys)
    return min(latitudes), min(longitudes), max(latitudes), max(longitudes)


def paths_command(argv):
    parser = argparse.ArgumentParser(description="Prepare inspectable runnable-path graphs")
    parser.add_argument(
        "--experiments", type=Path, default=Path("config/routing-experiments")
    )
    parser.add_argument("--regions", type=Path, default=Path("config/regions"))
    parser.add_argument("--data-root", type=Path, default=Path("data/paths"))
    parser.add_argument("--elevation-root", type=Path, default=Path("data/elevation"))
    parser.add_argument("--catalog", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="Acquire OSM and build a directed graph")
    prepare.add_argument("experiment_id")
    prepare.add_argument("--input", type=Path, help="Use an existing Overpass JSON response")
    prepare.add_argument("--dry-run", action="store_true")
    prepare.add_argument(
        "--without-terrain", action="store_true",
        help="Prepare topology only when derived terrain tiles are unavailable",
    )
    args = parser.parse_args(argv)
    experiment = find_experiment(args.experiments, args.experiment_id)
    bounds_wgs84 = _wgs84_bounds(experiment.bounds)
    boundary_refs = tuple(
        boundary["osm_ref"] for boundary in experiment.route_boundaries
    )
    if args.dry_run:
        print(f"Experiment {experiment.id}: EPSG:3067 {experiment.bounds}")
        print(f"Overpass WGS84 bounds: {bounds_wgs84}")
        print(highway_query(bounds_wgs84, boundary_refs))
        return

    boundary_key = "-".join(boundary_refs) if boundary_refs else "none"
    raw_path = (
        args.input
        or args.data_root / "raw" / experiment.id
        / f"osm-network-boundaries-{boundary_key}.json"
    )
    if args.input:
        payload = raw_path.read_bytes()
        osm_provenance = {
            "source_path": str(raw_path),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "bounds_wgs84": list(bounds_wgs84),
        }
    else:
        try:
            if not raw_path.exists():
                osm_provenance = download_highways(
                    bounds_wgs84, raw_path, boundary_refs
                )
            else:
                sidecar = raw_path.with_suffix(raw_path.suffix + ".json")
                if not sidecar.exists():
                    parser.error(f"Existing OSM source has no provenance sidecar: {raw_path}")
                osm_provenance = json.loads(sidecar.read_text())
                actual_digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                if actual_digest != osm_provenance.get("sha256"):
                    parser.error(f"Existing OSM source checksum does not match: {raw_path}")
        except HTTPError as exc:
            parser.exit(1, f"OSM download failed: HTTP {exc.code}; retry later or use --input.\n")
        except URLError:
            parser.exit(1, "OSM download failed: network connection unavailable.\n")
    try:
        osm_document = json.loads(raw_path.read_text())
        graph = build_routing_graph(
            osm_document,
            source={
                "experiment_id": experiment.id,
                "experiment_config_digest": experiment.config_digest,
                "osm": osm_provenance,
            },
            boundary_refs=boundary_refs,
        )
        graph["experiment"] = {
            "bounds_epsg3067": list(experiment.bounds),
            "request": experiment.request,
            "route_boundaries": list(experiment.route_boundaries),
            "area_note": experiment.area_note,
        }
        graph["start"] = {
            "name": experiment.start.name,
            "address": experiment.start.address,
            "longitude": experiment.start.longitude,
            "latitude": experiment.start.latitude,
        }
        graph["start_snap"] = snap_to_vertex(
            graph, experiment.start.longitude, experiment.start.latitude
        )
        if not args.without_terrain:
            terrain = experiment.terrain
            region = find_region(args.regions, terrain["elevation_region"])
            store = ElevationStore(args.elevation_root, args.catalog)
            manifest_id = store.active_manifest_for_region(region)
            terrain_paths = store.ready_derived_paths(manifest_id, terrain["wavelength_m"])
            if not terrain_paths:
                parser.error(
                    f"No prepared {terrain['wavelength_m']} m terrain tiles for "
                    f"{region.id}; run region precompute first"
                )
            enrich_graph_terrain(
                graph,
                terrain_paths,
                terrain["wavelength_m"],
                terrain["sampling_interval_m"],
            )
            graph["terrain"]["manifest_id"] = manifest_id
            if graph["terrain"]["valid_fraction"] < 1:
                parser.error(
                    "Prepared terrain does not cover every path sample; "
                    "inspect experiment bounds and derived tiles"
                )
        outputs = write_graph_outputs(graph, args.data_root / "prepared" / experiment.id)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Path graph preparation failed: {exc}\n")
    summary = graph["summary"]
    print(
        f"Prepared {experiment.id}: {summary['node_count']} nodes, "
        f"{summary['segment_count']} segments, {summary['arc_count']} directed arcs"
    )
    print(
        f"Components: {summary['component_count']}; largest "
        f"{summary['largest_component_nodes']} nodes; start snap "
        f"{graph['start_snap']['snap_distance_m']:.1f} m"
    )
    print(f"Inspection GeoJSON: {outputs['geojson']}")


def routes_command(argv):
    parser = argparse.ArgumentParser(description="Generate experimental running routes")
    parser.add_argument(
        "--experiments", type=Path, default=Path("config/routing-experiments")
    )
    parser.add_argument("--graph-root", type=Path, default=Path("data/paths/prepared"))
    parser.add_argument("--output-root", type=Path, default=Path("data/routes/prepared"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    baseline = subparsers.add_parser(
        "baseline", help="Generate anchor-based diverse-path loop alternatives"
    )
    baseline.add_argument("experiment_id")
    topology_parser = subparsers.add_parser(
        "topology", help="Analyze cyclic topology and enumerate test-area faces"
    )
    topology_parser.add_argument("experiment_id")
    face_parser = subparsers.add_parser(
        "faces", help="Generate loops from connected planar face regions"
    )
    face_parser.add_argument("experiment_id")
    catalog_parser = subparsers.add_parser(
        "catalog", help="Precompute reusable running cycles at several scales"
    )
    catalog_parser.add_argument("experiment_id")
    atomic_parser = subparsers.add_parser(
        "atomic", help="Inventory disjoint profile atomic regions and render classifications"
    )
    atomic_parser.add_argument("experiment_id")
    atomic_parser.add_argument("--plot", type=Path)
    microcycle_parser = subparsers.add_parser(
        "microcycles", help="Repair atomic regions into a disjoint microcycle partition"
    )
    microcycle_parser.add_argument("experiment_id")
    microcycle_parser.add_argument("--plot", type=Path)
    args = parser.parse_args(argv)
    experiment = find_experiment(args.experiments, args.experiment_id)
    graph_path = args.graph_root / experiment.id / "graph.json"
    try:
        graph = json.loads(graph_path.read_text())
        graph_digest = graph.get("source", {}).get("experiment_config_digest")
        if graph_digest != experiment.config_digest:
            parser.error(
                "Prepared graph does not match the current experiment configuration; "
                "run paths prepare again"
            )
        index = GraphIndex(graph)
        if args.command == "topology":
            result = prepare_topology(
                index, experiment.topology["face_test_bounds_epsg3067"]
            )
            outputs = write_topology_outputs(
                index, result, args.graph_root / experiment.id / "topology"
            )
        elif args.command in {"faces", "catalog", "atomic", "microcycles"}:
            topology_path = args.graph_root / experiment.id / "topology" / "topology.json"
            topology = json.loads(topology_path.read_text())
            if args.command == "microcycles":
                from runmap.experiments.plot_microcycle_partition import plot_microcycle_partition

                inventory = build_atomic_region_inventory(
                    index, topology, experiment.topology.get("face_search", {}),
                    experiment.topology.get("atomic_regions", {}),
                )
                write_atomic_region_outputs(index, inventory, args.output_root / experiment.id)
                result = build_microcycle_partition(
                    index, inventory, experiment.topology.get("microcycle_partition", {})
                )
                outputs = write_microcycle_outputs(index, result, args.output_root / experiment.id)
                outputs["plot"] = args.plot or Path("artifacts") / f"{experiment.id}-microcycle-partition.png"
                outputs["repairs_plot"] = outputs["plot"].with_name(outputs["plot"].stem + "-repairs.png")
                result["timings_seconds"]["rendering"] = plot_microcycle_partition(
                    index, inventory, result, outputs["plot"], outputs["repairs_plot"]
                )
                result["timings_seconds"]["atomic_inventory"] = inventory["timings_seconds"]["total"]
                result["timings_seconds"]["total"] = round(
                    result["timings_seconds"]["total"] + result["timings_seconds"]["rendering"]
                    + result["timings_seconds"]["atomic_inventory"], 6
                )
                write_microcycle_outputs(index, result, args.output_root / experiment.id)
            elif args.command == "atomic":
                from runmap.experiments.plot_atomic_regions import plot_atomic_regions

                result = build_atomic_region_inventory(
                    index, topology, experiment.topology.get("face_search", {}),
                    experiment.topology.get("atomic_regions", {}),
                )
                outputs = write_atomic_region_outputs(
                    index, result, args.output_root / experiment.id
                )
                outputs["plot"] = args.plot or Path("artifacts") / f"{experiment.id}-atomic-regions.png"
                result["timings_seconds"]["rendering"] = plot_atomic_regions(
                    index, result, outputs["plot"]
                )
                result["timings_seconds"]["total"] = round(
                    result["timings_seconds"]["total"] + result["timings_seconds"]["rendering"], 6
                )
                write_atomic_region_outputs(index, result, args.output_root / experiment.id)
            elif args.command == "catalog":
                result = generate_cycle_catalog(
                    index,
                    topology,
                    experiment.topology.get("face_search", {}),
                    experiment.topology.get("cycle_catalog", {}),
                )
                if not result["cycles"]:
                    parser.error("Cycle preprocessing produced no usable cycles")
                outputs = write_cycle_catalog_outputs(
                    index, result, args.output_root / experiment.id
                )
            else:
                result = generate_face_region_loops(
                    index,
                    topology,
                    experiment.request,
                    experiment.topology.get("face_search", {}),
                )
                if not result["alternatives"]:
                    parser.error("Face-region search produced no usable route alternatives")
                outputs = write_face_route_outputs(
                    index, result, args.output_root / experiment.id
                )
        else:
            result = generate_anchor_loops(index, experiment.request, experiment.baseline)
            if not result["alternatives"]:
                parser.error("Baseline search produced no route alternatives")
            outputs = write_route_outputs(
                index, result, args.output_root / experiment.id
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Route generation failed: {exc}\n")
    if args.command == "topology":
        summary = result["summary"]
        print(
            f"Topology: {summary['bridge_segment_count']} bridges, "
            f"{summary['core_segment_count']} 2-core segments, "
            f"{summary['cyclic_component_count']} cyclic components"
        )
        print(
            f"Face test: {summary['face_test_segment_count']} segments, "
            f"{summary['bounded_face_count']} bounded faces, "
            f"{summary['connector_corridor_count']} grade-separated connectors"
        )
        print(f"Face GeoJSON: {outputs['faces']}")
        return
    if args.command == "microcycles":
        summary = result["summary"]
        print(f"Microcycle partition: {summary['atomic_region_count']} atoms -> "
              f"{summary['active_region_count']} active regions; {summary['classification_counts']}")
        print(f"Repairs: {summary['merged_count']} merges; {summary['repaired_count']} repaired microcycles; "
              f"{summary['merge_attempt_count']} neighbor comparisons")
        print(f"Verified partition and merge tree; {summary['shared_interface_count']} runnable interfaces")
        print(f"Merge rejections: {result['merge_rejection_summary']}")
        print(f"Partition: {outputs['partition']}")
        print(f"Classification map: {outputs['plot']}")
        print(f"Repair comparison: {outputs['repairs_plot']}")
        return
    if args.command == "atomic":
        summary = result["summary"]
        counts = summary["classification_counts"]
        print(f"Atomic inventory: {summary['base_face_count']} base faces -> "
              f"{summary['atomic_region_count']} regions; {counts}")
        print(f"Verified partition and incidences; {summary['shared_interface_count']} runnable interfaces")
        print(f"Hole filling: {summary['filled_hole_count']} holes; "
              f"{summary['absorbed_profile_region_count']} enclosed profile regions absorbed")
        print(f"Rejections: {result['rejection_summary']}")
        print(f"Inventory: {outputs['inventory']}")
        print(f"Atomic GeoJSON: {outputs['geojson']}")
        print(f"Classification map: {outputs['plot']}")
        return
    if args.command == "faces":
        if result.get("profile_summary"):
            profile = result["profile_summary"]
            print(
                f"Profile topology: {profile['base_face_count']} base faces -> "
                f"{profile['superface_count']} usable superfaces; "
                f"{profile['dissolved_separator_count']} separators dissolved"
            )
        print(
            f"Evaluated {result['evaluated_candidate_count']} usable face-region "
            f"boundaries from {len(result['seed_face_ids'])} seeds; "
            f"{result['feasible_candidate_count']} satisfy all hard constraints; "
            f"{result['runtime_seconds']:.1f} s"
        )
        print(
            f"Growth: {result['expanded_growth_state_count']} beam states, "
            f"{result['proposed_growth_chunk_count']} face chunks proposed"
        )
        for alternative in result["alternatives"]:
            metrics = alternative["metrics"]
            shape = alternative["shape_metrics"]
            print(
                f"{alternative['id']}: {metrics['distance_m'] / 1000:.2f} km, "
                f"{metrics['ascent_m']:.1f} m ascent, "
                f"{metrics['repeated_fraction'] * 100:.1f}% repeated, "
                f"{metrics['stair_distance_m']:.0f} m stairs, "
                f"{shape['concave_turn_count']} concave turns, "
                f"{shape['maximum_turn_degrees']:.0f}° maximum turn, "
                f"{alternative['face_count']} faces, "
                f"feasible={alternative['feasible']}"
            )
        print(f"Route GeoJSON: {outputs['geojson']}")
        return
    if args.command == "catalog":
        print(
            f"Cycle catalog: {result['generated_cycle_count']} generated, "
            f"{result['selected_cycle_count']} selected from "
            f"{len(result['seed_face_ids'])} access-ordered seeds; "
            f"{result['runtime_seconds']:.1f} s"
        )
        print(
            f"Growth: {result['multiband_traversal_count']} multiband traversals, "
            f"{result['expanded_growth_state_count']} expanded states, "
            f"{result['pruned_growth_state_count']} pruned states, "
            f"{result['duplicate_growth_state_count']} duplicate proposals"
        )
        for band in result["band_summary"]:
            print(
                f"{band['target_cycle_length_m'] / 1000:.1f} km band: "
                f"{band['generated_cycle_count']} generated, "
                f"{band['selected_cycle_count']} selected"
            )
        composition = result.get("composition")
        if composition is not None:
            print(
                f"Composition: {composition['composition_count']} envelopes "
                f"from {len(composition['root_candidate_ids'])} roots; "
                f"levels {composition['level_summary']}"
            )
            print(f"Composition GeoJSON: {outputs['compositions']}")
        print(f"Catalog GeoJSON: {outputs['geojson']}")
        return
    print(
        f"Evaluated {result['evaluated_candidate_count']} routes through "
        f"{result['anchor_count']} anchors; "
        f"{result['feasible_candidate_count']} satisfy distance and repetition"
    )
    for alternative in result["alternatives"]:
        metrics = alternative["metrics"]
        print(
            f"{alternative['id']}: {metrics['distance_m'] / 1000:.2f} km, "
            f"{metrics['ascent_m']:.1f} m ascent, "
            f"{metrics['repeated_fraction'] * 100:.1f}% repeated, "
            f"feasible={alternative['feasible']}"
        )
    print(f"Route GeoJSON: {outputs['geojson']}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "region":
        region_command(argv[1:])
    elif argv and argv[0] == "paths":
        paths_command(argv[1:])
    elif argv and argv[0] == "routes":
        routes_command(argv[1:])
    else:
        legacy_download(argv)


if __name__ == "__main__":
    main()

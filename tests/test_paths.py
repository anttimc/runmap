import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform

from runmap.paths.experiments import load_experiment
from runmap.paths.graph import build_routing_graph, graph_geojson, snap_to_vertex
from runmap.paths.terrain import enrich_graph_terrain
from runmap.sources.osm import highway_query


def node(identifier, longitude, latitude, tags=None):
    value = {"type": "node", "id": identifier, "lon": longitude, "lat": latitude}
    if tags:
        value["tags"] = tags
    return value


class PathGraphTests(unittest.TestCase):
    def test_splits_at_shared_nodes_and_builds_pedestrian_arcs(self):
        document = {"elements": [
            node(1, 24.80, 60.20),
            node(2, 24.81, 60.20),
            node(3, 24.82, 60.20),
            node(4, 24.81, 60.19),
            node(5, 24.81, 60.21),
            node(6, 24.83, 60.20),
            node(7, 24.84, 60.20),
            node(8, 24.79, 60.20),
            node(9, 24.78, 60.20),
            node(10, 24.77, 60.20),
            node(11, 24.76, 60.20),
            {
                "type": "way", "id": 100, "nodes": [1, 2, 3],
                "tags": {"highway": "residential", "oneway": "yes"},
            },
            {
                "type": "way", "id": 101, "nodes": [4, 2, 5],
                "tags": {"highway": "footway", "bridge": "yes"},
            },
            {
                "type": "way", "id": 102, "nodes": [6, 7],
                "tags": {"highway": "service", "access": "private"},
            },
            {
                "type": "way", "id": 103, "nodes": [8, 9],
                "tags": {"highway": "path", "oneway:foot": "yes"},
            },
            {
                "type": "way", "id": 104, "nodes": [10, 11],
                "tags": {"highway": "cycleway"},
            },
        ]}

        graph = build_routing_graph(document)

        self.assertEqual(graph["summary"]["selected_way_count"], 3)
        self.assertEqual(
            graph["summary"]["rejected_way_counts"],
            {"access_denied": 1, "cycleway_without_foot_permission": 1},
        )
        self.assertEqual(graph["summary"]["segment_count"], 5)
        self.assertEqual(graph["summary"]["arc_count"], 9)
        road_segments = [
            segment for segment in graph["segments"]
            if segment["tags"].get("oneway") == "yes"
        ]
        road_ids = {segment["id"] for segment in road_segments}
        road_arcs = [arc for arc in graph["arcs"] if arc["segment_id"] in road_ids]
        self.assertEqual(len(road_arcs), 4, "ordinary vehicle oneway must not restrict pedestrians")
        foot_ids = {
            segment["id"] for segment in graph["segments"]
            if segment["tags"].get("oneway:foot") == "yes"
        }
        foot_oneway = [arc for arc in graph["arcs"] if arc["segment_id"] in foot_ids]
        self.assertEqual(len(foot_oneway), 1)
        self.assertEqual(graph["summary"]["bridge_segments"], 2)

    def test_geometric_crossing_without_shared_osm_node_stays_disconnected(self):
        document = {"elements": [
            node(1, 24.80, 60.20), node(2, 24.82, 60.20),
            node(3, 24.81, 60.19), node(4, 24.81, 60.21),
            {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "path"}},
            {"type": "way", "id": 11, "nodes": [3, 4], "tags": {"highway": "path"}},
        ]}

        graph = build_routing_graph(document)

        self.assertEqual(graph["summary"]["component_count"], 2)

    def test_rejects_indoor_and_parking_circulation(self):
        elements = [node(offset, 24.80 + offset / 1000, 60.20) for offset in range(1, 13)]
        elements.extend([
            {"type": "way", "id": 20, "nodes": [1, 2],
             "tags": {"highway": "footway", "indoor": "yes"}},
            {"type": "way", "id": 21, "nodes": [3, 4],
             "tags": {"highway": "footway", "level": "0"}},
            {"type": "way", "id": 22, "nodes": [5, 6],
             "tags": {"highway": "service", "service": "parking_aisle"}},
            {"type": "way", "id": 23, "nodes": [7, 8],
             "tags": {"highway": "service", "tunnel": "yes"}},
            {"type": "way", "id": 24, "nodes": [9, 10],
             "tags": {"highway": "footway", "tunnel": "yes", "level": "-1"}},
            {"type": "way", "id": 25, "nodes": [11, 12],
             "tags": {"highway": "footway", "tunnel": "building_passage"}},
        ])

        graph = build_routing_graph({"elements": elements})

        self.assertEqual(graph["summary"]["selected_way_count"], 1)
        self.assertEqual(graph["segments"][0]["osm_way_id"], "24")
        self.assertEqual(graph["summary"]["rejected_way_counts"], {
            "enclosed_service": 1,
            "indoor": 2,
            "level_geometry": 1,
            "parking_aisle": 1,
        })

    def test_preserves_configured_non_runnable_route_boundary(self):
        document = {"elements": [
            node(1, 24.80, 60.19), node(2, 24.80, 60.21),
            {
                "type": "way", "id": 101, "nodes": [1, 2],
                "tags": {"highway": "primary", "ref": "101", "name": "Kehä I"},
            },
        ]}

        graph = build_routing_graph(document, boundary_refs=("101",))

        self.assertEqual(graph["summary"]["segment_count"], 0)
        self.assertEqual(graph["summary"]["route_boundary_way_count"], 1)
        self.assertEqual(graph["route_boundaries"][0]["name"], "Kehä I")

    def test_snap_and_geojson_are_inspectable(self):
        document = {"elements": [
            node(1, 24.80, 60.20), node(2, 24.81, 60.20),
            {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "path"}},
        ]}
        graph = build_routing_graph(document)

        snapped = snap_to_vertex(graph, 24.80001, 60.20)
        geojson = graph_geojson(graph)

        self.assertEqual(snapped["id"], "1")
        self.assertLess(snapped["snap_distance_m"], 1)
        self.assertEqual(geojson["features"][0]["geometry"]["type"], "LineString")

    def test_integrates_directional_ascent_from_smoothed_gradient(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "terrain.tif"
            magnitude = np.full((100, 200), 1000, dtype=np.uint16)
            bearing = np.full((100, 200), 9000, dtype=np.uint16)
            with rasterio.open(
                path, "w", driver="GTiff", width=200, height=100, count=2,
                dtype="uint16", crs="EPSG:3067",
                transform=from_origin(374900, 6675200, 2, 2), nodata=65535,
            ) as dataset:
                dataset.write(magnitude, 1)
                dataset.write(bearing, 2)
            longitude, latitude = transform(
                "EPSG:3067", "EPSG:4326", [375000, 375100], [6675100, 6675100]
            )
            geometry = [[longitude[0], latitude[0]], [longitude[1], latitude[1]]]
            graph = {
                "segments": [{
                    "id": "s", "from": "1", "to": "2", "geometry": geometry,
                    "length_m": 100.0, "tags": {}, "forward": True, "backward": True,
                }],
                "arcs": [
                    {"id": "s:f", "segment_id": "s", "from": "1", "to": "2"},
                    {"id": "s:b", "segment_id": "s", "from": "2", "to": "1"},
                ],
                "summary": {},
            }

            enrich_graph_terrain(graph, [path], wavelength_m=40, sampling_interval_m=10)

        self.assertAlmostEqual(graph["arcs"][0]["ascent_m"], 10.0, places=2)
        self.assertAlmostEqual(graph["arcs"][0]["descent_m"], 0.0, places=2)
        self.assertAlmostEqual(graph["arcs"][1]["ascent_m"], 0.0, places=2)
        self.assertAlmostEqual(graph["arcs"][1]["descent_m"], 10.0, places=2)
        self.assertEqual(graph["terrain"]["valid_fraction"], 1.0)


class RoutingExperimentTests(unittest.TestCase):
    def test_loads_checked_experiment(self):
        document = {
            "schema_version": 1,
            "id": "test-loop",
            "title": "Test",
            "bounds_epsg3067": [370000, 6675000, 375000, 6680000],
            "start": {
                "name": "Start", "address": "Address",
                "latitude": 60.2, "longitude": 24.8,
            },
            "request": {"mode": "environment-loop"},
            "terrain": {
                "elevation_region": "leppavaara", "wavelength_m": 40,
                "sampling_interval_m": 10,
            },
            "baseline": {
                "anchor_count": 8, "alternative_count": 3,
                "ascent_costs_per_m": [20], "stairs_multiplier": 8,
                "overlap_factors": [3, 10],
            },
            "route_boundaries": [
                {"osm_ref": "1", "behavior": "do-not-cross", "name": "Ring I"}
            ],
            "topology": {
                "face_test_bounds_epsg3067": [370000, 6675000, 375000, 6680000]
            },
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test-loop.json"
            path.write_text(json.dumps(document))
            experiment = load_experiment(path)

        self.assertEqual(experiment.start.name, "Start")
        self.assertEqual(experiment.terrain["wavelength_m"], 40)
        self.assertEqual(experiment.baseline["anchor_count"], 8)
        self.assertEqual(experiment.route_boundaries[0]["osm_ref"], "1")
        self.assertEqual(experiment.topology["face_test_bounds_epsg3067"][0], 370000)

    def test_overpass_query_uses_explicit_bounds(self):
        query = highway_query((60.1, 24.7, 60.3, 24.9), ("1",))

        self.assertIn(
            'way["highway"~"^(cycleway|footway|living_street|path|pedestrian|residential|service|steps|track|unclassified)$"]'
            '(60.1000000,24.7000000,60.3000000,24.9000000)',
            query,
        )
        self.assertIn('way["highway"]["ref"~"^(1)$"]', query)
        self.assertIn("out body", query)


if __name__ == "__main__":
    unittest.main()

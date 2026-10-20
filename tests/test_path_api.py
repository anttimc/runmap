"""Bounded path queries, inspection semantics, and optional API integration."""
import copy
import json
from dataclasses import replace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runmap.api.app import app, lifespan
from runmap.api.paths import (load_path_data, path_config, viewport_paths, inspect_path,
                              uphill_sections, render_paths, explore_paths, snap_path_start, _build_display_index)
from runmap.paths.graph import write_graph_outputs
from runmap.paths.measurements import graph_digest
from runmap.routing.profiles import surface_category, exploration_profile
from runmap.routing.search import apply_profile
from runmap.paths.exploration import legal_directions


def fixture():
    geometry = [[24.8, 60.2], [24.801, 60.2], [24.802, 60.2], [24.803, 60.2], [24.804, 60.2]]
    segment = {"id": "s", "osm_way_id": "1", "from": "1", "to": "2", "geometry": geometry,
               "length_m": 220, "tags": {"highway": "path", "name": "Test path", "surface": "gravel"},
               "forward": True, "backward": False}
    intervals = []
    for i, grade in enumerate([2, 4, None, -8]):
        intervals.append({"segment_id": "s", "interval_index": i, "start_m": i * 50,
                          "end_m": (i + 1) * 50, "graph_start_m": i * 55, "graph_end_m": (i + 1) * 55,
                          "grade_percent": grade, "height_change_m": grade / 2 if grade is not None else None,
                          "valid": grade is not None, "quality_flags": ["missing_terrain"] if grade is None else [],
                          "geometry": geometry[i:i+2]})
    graph = {"schema_version": 1, "segments": [segment],
             "nodes": [{"id": "1", "longitude": 24.8, "latitude": 60.2},
                       {"id": "2", "longitude": 24.804, "latitude": 60.2}],
             "arcs": [{"id": "s:f", "segment_id": "s", "from": "1", "to": "2", "length_m": 220}], "summary": {},
             "source": {"osm": {"sha256": "fixture"}},
             "_measurements": {"schema_version": 1, "segments": {"s": intervals},
                               "method": {"wavelength_m": 40, "sampling_interval_m": 10, "manifest_id": "fixture"}}}
    return graph


class PathQueryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.graph = fixture()
        self.paths = write_graph_outputs(self.graph, self.directory.name)
        self.data = load_path_data(self.paths["graph"])

    def test_config_identity_and_source_settings(self):
        config = path_config(self.data)
        self.assertEqual(config["default_budget_km"], 3)
        self.assertEqual(config["dataset_id"], graph_digest(self.graph))
        self.assertEqual(config["method"]["manifest_id"], "fixture")
        self.assertEqual(config["grade_bands_percent"], [0, 2, 5, 8])
        self.assertEqual(config["coverage"]["kind"], "geometry_envelope")

    def test_building_filter_precedes_interval_work_and_preserves_start(self) -> None:
        from runmap.routing.search import build_graph_index
        graph = copy.deepcopy(self.data.graph)
        graph["segments"][0]["tags"] = {"highway": "service", "service": "driveway"}
        # A physical self-loop anchors the dangling segment to a surviving core.
        loop = {**graph["segments"][0], "id": "loop", "to": "1"}
        graph["segments"].append(loop)
        graph["arcs"].append({"id": "loop:f", "segment_id": "loop", "from": "1", "to": "1", "length_m": 220})
        coverage = {"kind": "prepared_extract", "geometry": {"type": "Polygon", "coordinates": [[
            [24.7, 60.1], [24.9, 60.1], [24.9, 60.3], [24.7, 60.3], [24.7, 60.1]]]}}
        data = _build_display_index(replace(self.data, graph=graph, routing=build_graph_index(graph),
                                            coverage=coverage, bounds=(24.7, 60.1, 24.9, 60.3)))
        self.assertEqual(set(data.building_access), {"s"})
        bounds = (24.799, 60.199, 24.805, 60.201)
        for surface in ("all", "surfaced"):
            for stairs in (True, False):
                hidden = viewport_paths(data, bounds, 15, surface, max_features=0, avoid_stairs=stairs)
                self.assertEqual((hidden["status"], hidden["features"]), ("ready", []))
        restored = viewport_paths(data, bounds, 15, hide_building_access=False)
        self.assertEqual(len(restored["features"]), 4)
        self.assertEqual(viewport_paths(data, bounds, 15, max_features=0,
                                       hide_building_access=False)["reason"], "feature_limit")
        for metric in ("gradient", "distance"):
            self.assertFalse(render_paths(viewport_paths(data, bounds, 15), metric)["features"])
            with patch("runmap.api.paths._interval_state", side_effect=AssertionError("hidden interval evaluated")):
                result = explore_paths(data, bounds, 15, 24.801, 60.2)
                self.assertEqual(result["status"], "ready")
                self.assertFalse(render_paths(result, metric)["features"])
            result = explore_paths(data, bounds, 15, 24.801, 60.2, hide_building_access=False)
            self.assertEqual(len(result["features"]), 4)
            self.assertTrue(result["distance_cache_hit"])
        self.assertEqual(snap_path_start(data, 24.801, 60.2)["snap"]["segment_id"], "s")
        inspection = inspect_path(data, "s")
        self.assertTrue(inspection["eligible"])
        self.assertTrue(inspection["hidden_by_building_access"])
        self.assertEqual(inspection["visibility_reason"], "building_access_driveway")
        self.assertFalse(inspect_path(data, "s", hide_building_access=False)["hidden_by_building_access"])
        self.assertEqual(path_config(data)["building_access_filter"]["hidden_intervals"], 4)

    def test_viewport_returns_complete_intervals_including_crossings(self):
        result = viewport_paths(self.data, (24.8004, 60.1999, 24.8006, 60.2001), 15)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["features"]), 1)
        self.assertEqual(result["features"][0]["geometry"]["coordinates"], [[24.8, 60.2], [24.801, 60.2]])
        self.assertEqual(result["features"][0]["id"], "s:0")
        self.assertEqual(result["features"][0]["properties"]["surface_class"], "gravel")
        self.assertTrue(result["coverage_limited"])

    def test_direction_cache_is_reset_for_new_policy(self):
        index = self.data.routing
        self.assertEqual(legal_directions(index, "s"), {True})
        changed = apply_profile(index, {**exploration_profile(), "forbidden_surfaces": ["gravel"]})
        self.assertIsNot(index.direction_cache, changed.direction_cache)
        self.assertEqual(legal_directions(changed, "s"), set())
        self.assertEqual(legal_directions(index, "s"), {True})

    def test_render_merges_equal_grades_without_changing_measurements(self):
        response = viewport_paths(self.data, (24.79, 60.19, 24.81, 60.21), 11)
        response["features"][0]["properties"]["grade_percent"] = 2
        response["features"][1]["properties"]["grade_percent"] = 2
        response["features"][1]["properties"]["flat_min_grade_50m"] = 2
        before = copy.deepcopy(response)
        rendered = render_paths(response)
        self.assertEqual(rendered["status"], "ready")
        self.assertEqual(rendered["measurement_interval_count"], 4)
        self.assertEqual(len(rendered["features"]), 3)
        first = rendered["features"][0]
        self.assertEqual(first["geometry"]["coordinates"], self.graph["segments"][0]["geometry"][:3])
        self.assertEqual(first["properties"]["grade_percent"], 2)
        self.assertEqual(response, before)

    def test_render_preserves_budget_transitions_and_segment_boundaries(self):
        response = viewport_paths(self.data, (24.79, 60.19, 24.81, 60.21), 11)
        first, second = response["features"][:2]
        first["properties"]["inclusion_state"] = "included"
        second["properties"]["inclusion_state"] = "beyond_budget"
        self.assertEqual(len(render_paths(response)["features"]), 4)
        second["properties"]["inclusion_state"] = "included"
        second["properties"]["segment_id"] = "other"
        self.assertEqual(len(render_paths(response)["features"]), 4)

    def test_render_preserves_distinct_grades_within_old_bands(self):
        response = viewport_paths(self.data, (24.79, 60.19, 24.81, 60.21), 11)
        response["features"][0]["properties"]["grade_percent"] = 2.1
        response["features"][1]["properties"]["grade_percent"] = 2.9
        rendered = render_paths(response)
        self.assertEqual([feature["properties"]["grade_percent"]
                          for feature in rendered["features"][:2]], [2.1, 2.9])

    def test_distance_render_preserves_exact_distances(self):
        response = viewport_paths(self.data, (24.79, 60.19, 24.81, 60.21), 11)
        for feature, distance in zip(response["features"], [999, 1000, 2000, None]):
            feature["properties"]["distance_from_start_m"] = distance
        rendered = render_paths(response, "distance")
        self.assertEqual([f["properties"]["distance_from_start_m"] for f in rendered["features"]],
                         [999, 1000, 2000, None])
        self.assertNotIn("grade_percent", rendered["features"][0]["properties"])
        with self.assertRaises(ValueError):
            render_paths(response, "unknown")

    def test_zoom_area_feature_limit_and_outside_are_explicit(self):
        bounds = (24.79, 60.19, 24.81, 60.21)
        self.assertEqual(viewport_paths(self.data, bounds, 10)["status"], "zoom_in")
        result = viewport_paths(self.data, bounds, 15, max_features=1)
        self.assertEqual(result["reason"], "feature_limit")
        self.assertEqual(result["features"], [])
        self.assertEqual(viewport_paths(self.data, (24, 60, 26, 61), 15)["reason"], "area")
        self.assertEqual(viewport_paths(self.data, (25, 60, 26, 61), 15)["status"], "outside_coverage")
        for bounds in [(24.8, 60.2, 24.7, 60.3), (24.8, float("nan"), 25, 61)]:
            with self.assertRaises(ValueError):
                viewport_paths(self.data, bounds, 15)

    def test_rotated_coverage_envelope_does_not_claim_corner_coverage(self):
        ring = [[24.8, 60.2], [24.802, 60.198], [24.804, 60.2], [24.802, 60.202], [24.8, 60.2]]
        data = replace(self.data, coverage={"geometry": {"type": "Polygon", "coordinates": [ring]}, "kind": "prepared_extract"},
                       bounds=(24.8, 60.198, 24.804, 60.202))
        result = viewport_paths(data, (24.8001, 60.2015, 24.8002, 60.2016), 15)
        self.assertEqual(result["status"], "outside_coverage")

    def test_inspection_reverse_gaps_uphill_sections_and_provenance(self):
        forward = inspect_path(self.data, "s")
        reverse = inspect_path(self.data, "s", reverse=True)
        self.assertEqual(forward["ascent_m"], reverse["descent_m"])
        self.assertEqual(forward["descent_m"], reverse["ascent_m"])
        self.assertEqual(forward["valid_length_fraction"], .75)
        self.assertFalse(forward["totals_complete"])
        self.assertIsNone(forward["intervals"][2]["relative_end_m"])
        self.assertEqual(forward["intervals"][3]["relative_start_m"], 0)
        section = forward["uphill_sections"][0]
        self.assertEqual((section["length_m"], section["gain_m"], section["mean_grade_percent"], section["peak_grade_percent"]), (100, 3, 3, 4))
        self.assertEqual(reverse["uphill_sections"][0]["gain_m"], 4)
        self.assertEqual(forward["source"], self.graph["source"])
        self.assertFalse(forward["segment"]["backward"])
        self.assertEqual(self.data.measurements.document["segments"]["s"][0]["grade_percent"], 2)

    def test_quality_and_surface_unknowns_are_not_flat(self):
        graph = fixture()
        graph["segments"][0]["tags"].pop("surface")
        graph["_measurements"]["segments"]["s"][0]["quality_flags"] = ["bridge"]
        graph["_measurements"]["segments"]["s"][0]["grade_percent"] = 0
        paths = write_graph_outputs(graph, self.directory.name)
        data = load_path_data(paths["graph"])
        result = viewport_paths(data, (24.79, 60.19, 24.81, 60.21), 15)
        self.assertEqual(result["features"][0]["properties"]["quality"], "uncertain")
        self.assertEqual(result["features"][2]["properties"]["quality"], "missing")
        self.assertEqual(result["features"][0]["properties"]["surface_class"], "unknown")
        self.assertTrue(all(not feature["properties"]["eligible"] for feature in viewport_paths(data, (24.79, 60.19, 24.81, 60.21), 15, "surfaced")["features"]))
        self.assertEqual(surface_category({"tags": {"highway": "footway"}}), "unknown")

    def test_hills_do_not_bridge_missing_samples(self):
        intervals = copy.deepcopy(self.data.measurements.document["segments"]["s"])
        intervals[-1].update(grade_percent=5, height_change_m=2.5, quality_flags=["tunnel"])
        sections = uphill_sections(intervals)
        self.assertEqual(len(sections), 2)
        self.assertTrue(sections[1]["uncertain"])


async def get(url):
    path, _, query = url.partition("?")
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
               "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
               "query_string": query.encode(), "headers": [], "server": ("test", 80), "client": ("test", 1)}, receive, send)
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, json.loads(body)


class PathEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_building_filter_option_on_all_endpoints(self) -> None:
        from runmap.paths.dead_ends import Classification
        with TemporaryDirectory() as directory:
            paths = write_graph_outputs(fixture(), directory)
            data = load_path_data(paths["graph"])
            app.state.paths = replace(data, display_cells={}, building_access={
                "s": Classification("building_access_entrance", "s", "1", 100, ("entrance=yes",))})
            query = "west=24.799&south=60.199&east=24.805&north=60.201&zoom=15"
            for endpoint in (f"viewport?{query}",
                             f"explore?{query}&longitude=24.801&latitude=60.2&metric=gradient",
                             f"explore?{query}&longitude=24.801&latitude=60.2&metric=distance"):
                code, hidden = await get(f"/api/paths/{endpoint}")
                self.assertEqual((code, hidden["status"], hidden["features"]), (200, "ready", []))
                _, visible = await get(f"/api/paths/{endpoint}&hide_building_access=false")
                self.assertEqual({item["properties"]["segment_id"] for item in visible["features"]}, {"s"})
            self.assertTrue((await get("/api/paths/segments/s"))[1]["hidden_by_building_access"])
            self.assertFalse((await get("/api/paths/segments/s?hide_building_access=false"))[1]["hidden_by_building_access"])

    async def test_api_success_missing_invalid_and_stale_identity(self):
        with TemporaryDirectory() as directory:
            paths = write_graph_outputs(fixture(), directory)
            app.state.paths = load_path_data(paths["graph"])
            self.assertEqual((await get("/api/paths/config"))[1]["status"], "ready")
            self.assertEqual((await get("/api/paths/segments/s?reverse=true"))[1]["reverse"], True)
            self.assertEqual((await get("/api/paths/segments/missing"))[0], 404)
            self.assertEqual((await get("/api/paths/segments/s?dataset_id=stale"))[0], 409)
            self.assertEqual((await get("/api/paths/viewport?west=24.79&south=60.19&east=24.81&north=60.21&zoom=15"))[0], 200)
            self.assertEqual((await get("/api/paths/viewport?west=25&south=61&east=24&north=60&zoom=15"))[0], 400)
            self.assertEqual((await get("/api/paths/viewport"))[0], 422)
            app.state.paths = None
            app.state.path_status = "unavailable"
            self.assertEqual((await get("/api/paths/config"))[1]["status"], "unavailable")
            self.assertEqual((await get("/api/paths/segments/s"))[0], 503)

    async def test_snap_explore_and_inspection_share_profile_and_dataset(self):
        with TemporaryDirectory() as directory:
            paths = write_graph_outputs(fixture(), directory)
            app.state.paths = load_path_data(paths["graph"])
            base = "longitude=24.802&latitude=60.2"
            code, snapped = await get(f"/api/paths/snap?{base}")
            self.assertEqual(code, 200)
            self.assertEqual(snapped["status"], "ready")
            query = f"west=24.799&south=60.199&east=24.805&north=60.201&zoom=15&{base}"
            code, result = await get(f"/api/paths/explore?{query}")
            self.assertEqual(code, 200)
            self.assertEqual({feature["properties"]["inclusion_state"] for feature in result["features"]}, {"included", "beyond_budget"})
            self.assertEqual((await get(f"/api/paths/explore?{query}&dataset_id=stale"))[0], 409)
            self.assertEqual((await get(f"/api/paths/explore?{query}&budget_km=0"))[0], 422)
            self.assertEqual((await get(f"/api/paths/snap?{base}&surface=unknown"))[0], 400)
            self.assertEqual((await get(f"/api/paths/snap?{base}&segment_id=missing"))[1]["status"], "no_eligible_snap")
            code, inspected = await get(f"/api/paths/segments/s?reverse=true&{base}")
            self.assertEqual(code, 200)
            self.assertEqual({item["inclusion_state"] for item in inspected["intervals"]}, {"included", "beyond_budget"})
            self.assertEqual((await get("/api/paths/segments/s?longitude=24.8"))[0], 400)

    async def test_missing_or_corrupt_artifacts_do_not_break_terrain_startup(self):
        for error, expected in [(FileNotFoundError(), "unavailable"), (ValueError("identity mismatch"), "failed"), (KeyError("segments"), "failed")]:
            with patch.dict("os.environ", {"RUNMAP_ELEVATION_PATH": "fixture"}), \
                 patch("runmap.api.app.TerrainRenderer.from_raster", return_value="terrain"), \
                 patch("runmap.api.app.load_path_data", side_effect=error), \
                 patch("runmap.api.app.logging.getLogger"):
                async with lifespan(app):
                    self.assertEqual(app.state.terrain, "terrain")
                    self.assertIsNone(app.state.paths)
                    self.assertEqual(app.state.path_status, expected)


if __name__ == "__main__":
    unittest.main()

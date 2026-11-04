"""Validate overlapping metric proxies and distinguish candidate evidence from labels."""

from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.warp import transform, transform_geom
from shapely.geometry import box, LineString, Polygon, mapping

from runmap.api.environment_rasters import raster_config, raster_image, raster_sample, load_evidence
from runmap.environment.fields import build_fields, make_grid
from runmap.environment.objects import EnvironmentObject, categories, road_class
from runmap.environment.prepare import DEFAULT_CONFIG, load_config, prepare
from runmap.environment.noise import source_intensity, intensity_to_db, discomfort
from runmap.sources.environment import file_identity


class EnvironmentFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(DEFAULT_CONFIG.with_name("baseline-v1.json"))
        self.grid = make_grid([0, 0, 1000, 1000], 10)

    def test_woodland_holes_and_wetlands_remain_independent(self) -> None:
        outer = box(100, 100, 900, 900)
        polygon = Polygon(outer.exterior.coords, [box(450, 450, 550, 550).exterior.coords])
        objects = [EnvironmentObject("way/1", {}, polygon, ("forest",)),
                   EnvironmentObject("way/2", {}, box(200, 200, 400, 400), ("wetland",))]
        fields = build_fields(objects, self.grid, self.config)
        self.assertEqual(fields["woodland_coverage"][50, 50], 0)
        self.assertEqual(fields["woodland_coverage"][70, 30], 1)
        self.assertEqual(fields["wetland"][70, 30], 1)
        self.assertAlmostEqual(float(fields["forest"][70, 30]), 1, places=5)
        self.assertEqual(fields["context_support"][0, 0], 0)
        self.assertEqual(fields["context_support"][50, 50], 1)

    def test_park_is_candidate_and_stream_does_not_create_water_score(self) -> None:
        self.assertEqual(categories({"waterway": "ditch"}, "LineString"), ())
        self.assertEqual(categories({"leisure": "nature_reserve"}, "Polygon"), ("nature_reserve",))
        self.assertEqual(categories({"natural": "water"}, "Polygon"), ("other_water",))
        objects = [EnvironmentObject("way/1", {}, box(200, 200, 800, 800), ("park", "other_water"))]
        fields = build_fields(objects, self.grid, self.config)
        self.assertEqual(fields["forest"][50, 50], 0)
        self.assertAlmostEqual(float(fields["forest_evidence_gap"][50, 50]), 1, places=5)
        self.assertEqual(fields["water"][50, 50], 0)
        self.assertEqual(fields["water_evidence_gap"][50, 50], 1)
        self.assertTrue(np.isinf(fields["lake_distance_m"]).all())

    def test_park_coverage_preserves_holes_and_overlaps_woodland(self) -> None:
        polygon = Polygon(box(200, 200, 800, 800).exterior.coords,
                          [box(450, 450, 550, 550).exterior.coords])
        objects = [EnvironmentObject("way/1", {"leisure": "park"}, polygon, ("park",)),
                   EnvironmentObject("way/2", {"natural": "wood"}, box(200, 200, 400, 400), ("forest",))]
        fields = build_fields(objects, self.grid, self.config)
        self.assertEqual(fields["park"][50, 50], 0)
        self.assertEqual(fields["park"][70, 30], 1)
        self.assertEqual(fields["woodland_coverage"][70, 30], 1)
        self.assertEqual(fields["park"][10, 10], 0)

    def test_reserve_designation_alone_is_neither_park_nor_forest(self) -> None:
        objects = [EnvironmentObject("way/1", {"leisure": "nature_reserve"},
                                     box(200, 200, 800, 800), ("nature_reserve",))]
        fields = build_fields(objects, self.grid, self.config)
        self.assertEqual(fields["park"][50, 50], 0)
        self.assertEqual(fields["forest"][50, 50], 0)
        self.assertAlmostEqual(float(fields["forest_evidence_gap"][50, 50]), 1, places=5)
        self.assertEqual(categories({"leisure": "nature_reserve", "natural": "wood"}, "Polygon"),
                         ("forest", "nature_reserve"))

    def test_greenery_weights_designations_only_inside_and_does_not_add_overlaps(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        polygon = Polygon(box(200, 200, 800, 800).exterior.coords,
                          [box(450, 450, 550, 550).exterior.coords])
        objects = [EnvironmentObject("way/1", {"leisure": "park"}, polygon, ("park",)),
                   EnvironmentObject("way/2", {"leisure": "nature_reserve"}, polygon, ("nature_reserve",))]
        fields = build_fields(objects, self.grid, config)
        self.assertEqual(fields["greenery"][50, 50], 0)
        self.assertEqual(fields["greenery"][70, 30], 0.5)
        self.assertEqual(fields["greenery"][50, 19], 0)
        objects.append(EnvironmentObject("way/3", {}, box(100, 100, 900, 900), ("forest",)))
        wooded = build_fields(objects, self.grid, config)
        self.assertAlmostEqual(float(wooded["greenery"][50, 50]), 1, places=5)
        self.assertTrue(np.all(wooded["greenery"] <= 1))
        np.testing.assert_array_equal(wooded["review_candidates"], 1 - wooded["traffic_context_support"])

    def test_all_water_polygons_contribute_but_line_waterways_do_not(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        self.assertEqual(categories({"waterway": "riverbank"}, "Polygon"), ("other_water",))
        self.assertEqual(categories({"waterway": "ditch"}, "LineString"), ())
        objects = [EnvironmentObject("way/1", {"water": "pond"}, box(200, 200, 600, 800), ("other_water",))]
        fields = build_fields(objects, self.grid, config)
        self.assertEqual(fields["water"][50, 20], 1)
        self.assertEqual(fields["water_interior"][50, 40], 1)
        self.assertTrue((fields["water_evidence_gap"] == 0).all())
        self.assertTrue(np.isinf(fields["lake_distance_m"]).all())

    def test_water_uses_lake_boundary_and_sea_lines(self) -> None:
        objects = [EnvironmentObject("way/1", {}, box(200, 200, 600, 800), ("lake",)),
                   EnvironmentObject("way/2", {}, LineString([(900, 0), (900, 1000)]), ("sea",))]
        fields = build_fields(objects, self.grid, self.config)
        self.assertEqual(fields["water_interior"][50, 40], 1)
        self.assertEqual(fields["water"][50, 40], 0)
        self.assertEqual(fields["water"][50, 20], 1)
        self.assertEqual(fields["water"][50, 90], 1)
        self.assertLess(fields["water"][50, 95], fields["water"][50, 90])

    def test_road_class_weighting_deduplication_and_access(self) -> None:
        motorway = EnvironmentObject("way/1", {"highway": "motorway_link"}, LineString([(100, 0), (100, 1000)]), ("road",))
        street = EnvironmentObject("way/2", {"highway": "secondary", "maxspeed": "40"}, LineString([(800, 0), (800, 1000)]), ("road",))
        forbidden = replace(street, tags={"highway": "motorway", "motor_vehicle": "no"})
        self.assertIsNone(road_class(forbidden))
        fields = build_fields([motorway, street, street, forbidden], self.grid, self.config)
        self.assertAlmostEqual(float(fields["traffic"][50, 10]), 1)
        self.assertAlmostEqual(float(fields["traffic"][50, 80]), 0.6, places=6)
        self.assertLess(fields["traffic"][50, 85], fields["traffic"][50, 80])

    def test_reference_car_and_line_point_distance_doubling(self) -> None:
        noise = load_config(DEFAULT_CONFIG).road_noise
        self.assertIsNotNone(noise)
        distances = np.array([0, 5, 10, 20, 100], dtype=np.float32)
        line = intensity_to_db(source_intensity(distances, 1, 1, noise), noise)
        point = intensity_to_db(source_intensity(distances, 1, 2, noise), noise)
        np.testing.assert_allclose(line[:3], [70, 70, 70])
        self.assertAlmostEqual(float(line[2] - line[3]), 3.0103, places=4)
        self.assertAlmostEqual(float(point[2] - point[3]), 6.0206, places=4)
        self.assertAlmostEqual(float(line[4]), 60, places=4)
        self.assertAlmostEqual(float(point[4]), 50, places=4)
        attenuated = intensity_to_db(source_intensity(distances, 1, noise.spreading_exponents["motorway"], noise), noise)
        self.assertAlmostEqual(float(attenuated[2] - attenuated[3]), 4.51545, places=4)
        self.assertAlmostEqual(float(attenuated[4]), 55, places=4)
        self.assertGreater(float(attenuated[3]), float(point[3]))
        self.assertLess(float(attenuated[3]), float(line[3]))
        weighted = intensity_to_db(source_intensity(10, 0.2, 2, noise), noise)
        self.assertAlmostEqual(float(weighted), 63.0103, places=4)
        np.testing.assert_array_equal(discomfort(np.array([30, 45, 60, 75, 100], dtype=np.float32), noise), [0, 0, 0.5, 1, 1])

    def test_noise_has_no_300m_cutoff_and_reports_distant_context_risk(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        road = EnvironmentObject("way/1", {"highway": "motorway"}, LineString([(500, 0), (500, 1000)]), ("road",))
        fields = build_fields([road, road], self.grid, config)
        self.assertGreater(fields["traffic"][50, 90], 0)
        self.assertAlmostEqual(float(fields["traffic_noise_db"][50, 50]), 70)
        self.assertEqual(fields["traffic_context_support"][50, 50], 1)
        self.assertEqual(fields["traffic_context_support"][0, 0], 0)
        self.assertTrue(np.all((fields["traffic"] >= 0) & (fields["traffic"] <= 1)))
        absent = build_fields([], self.grid, config)
        self.assertTrue((absent["traffic"] == 0).all())
        self.assertEqual(absent["traffic_context_support"][0, 0], 0)
        self.assertEqual(absent["traffic_context_support"][50, 50], 1)

    def test_noise_configuration_rejects_invalid_reference_and_modes(self) -> None:
        document = json.loads(DEFAULT_CONFIG.read_text())
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            for field, value in [("car_intensity_w_m2", 0), ("reference_distance_m", float("nan")),
                                 ("discomfort_full_db", 40), ("spreading_exponents", {}),
                                 ("spreading_exponents", {**document["road_noise"]["spreading_exponents"], "motorway": float("nan")}),
                                 ("spreading_exponents", {**document["road_noise"]["spreading_exponents"], "motorway": 0.9}),
                                 ("spreading_exponents", {**document["road_noise"]["spreading_exponents"], "motorway": 2.1})]:
                changed = {**document, "road_noise": {**document["road_noise"], field: value}}
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_bundle_sampling_identity_and_no_overwrite(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            output = Path(temporary) / "fields"
            source.mkdir()
            geometry = transform_geom("EPSG:3067", "EPSG:4326", mapping(box(373800, 6673800, 374400, 6674400)))
            feature = {"type": "Feature", "properties": {"osm_type": "way", "osm_id": 1, "natural": "wood", "leisure": "nature_reserve"}, "geometry": geometry}
            road_geometry = transform_geom("EPSG:3067", "EPSG:4326", mapping(LineString([(374100, 6673800), (374100, 6674400)])))
            road = {"type": "Feature", "properties": {"osm_type": "way", "osm_id": 2, "highway": "motorway"}, "geometry": road_geometry}
            park = {"type": "Feature", "properties": {"osm_type": "way", "osm_id": 3, "leisure": "park"}, "geometry": geometry}
            pond_geometry = transform_geom("EPSG:3067", "EPSG:4326", mapping(box(374080, 6674080, 374120, 6674120)))
            pond = {"type": "Feature", "properties": {"osm_type": "way", "osm_id": 4, "natural": "water", "water": "pond"}, "geometry": pond_geometry}
            (source / "features.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [feature, road, park, pond]}))
            manifest = {"features": file_identity(source / "features.geojson"),
                        "request": {"bounds_epsg3067": [374000, 6674000, 374200, 6674200], "halo_m": 300},
                        "osm_base_timestamps": ["fixture"]}
            (source / "manifest.json").write_text(json.dumps(manifest))
            result = prepare(source, DEFAULT_CONFIG, output)
            prepared = json.loads(result.read_text())
            self.assertEqual(prepared["grid_shape"], [20, 20])
            with rasterio.open(output / "fields.tif") as raster:
                self.assertEqual(raster.crs.to_epsg(), 3067)
                self.assertTrue((raster.read(raster.descriptions.index("context_support") + 1) == 1).all())
            with self.assertRaises(FileExistsError):
                prepare(source, DEFAULT_CONFIG, output)
            with patch.dict(os.environ, {"RUNMAP_ENVIRONMENT_DIRECTORY": str(source), "RUNMAP_ENVIRONMENT_RASTER_DIRECTORY": str(output)}):
                config = raster_config(manifest["features"]["sha256"])
                self.assertIn(prepared["dataset_id"], str(config["sample_url"]))
                lon, lat = transform("EPSG:3067", "EPSG:4326", [374100], [6674100])
                sample = raster_sample(prepared["dataset_id"], lon[0], lat[0])
                self.assertAlmostEqual(sample["values"]["forest"], 1, places=5)
                self.assertEqual(sample["evidence"]["forest"]["count"], 1)
                self.assertEqual(sample["values"]["park"], 1)
                self.assertAlmostEqual(sample["values"]["greenery"], 1, places=5)
                self.assertEqual(sample["evidence"]["greenery"]["count"], 2)
                self.assertGreater(sample["values"]["water"], 0)
                self.assertEqual(sample["evidence"]["water"]["count"], 1)
                self.assertEqual(sample["evidence"]["water"]["nearest_or_strongest"][0]["id"], "way/4")
                self.assertEqual(sample["evidence"]["park"]["count"], 1)
                self.assertTrue((output / "park.png").is_file())
                self.assertTrue((output / "greenery.png").is_file())
                self.assertAlmostEqual(sample["values"]["traffic_noise_db"], 70, places=4)
                evidence = sample["evidence"]["traffic"]["nearest_or_strongest"][0]
                self.assertEqual(evidence["id"], "way/2")
                self.assertAlmostEqual(evidence["modeled_noise_db"], 70, places=4)
                self.assertEqual(evidence["spreading_exponent"], 1.5)
                self.assertEqual(raster_sample(prepared["dataset_id"], 0, 0)["status"], "outside_pilot")
                self.assertEqual(raster_image(prepared["dataset_id"], "greenery").path, output / "greenery.png")
                from fastapi import HTTPException
                with self.assertRaises(HTTPException) as stale:
                    raster_sample("outdated", lon[0], lat[0])
                self.assertEqual(stale.exception.status_code, 409)
                with self.assertRaises(HTTPException):
                    raster_image(prepared["dataset_id"], "../evidence")
                changed = {**manifest, "osm_base_timestamps": ["changed"]}
                (source / "manifest.json").write_text(json.dumps(changed))
                with self.assertRaises(HTTPException) as source_changed:
                    raster_sample(prepared["dataset_id"], lon[0], lat[0])
                self.assertEqual(source_changed.exception.status_code, 409)
            # Source bounds/halo/provenance must participate even when features are identical.
            second = prepare(source, DEFAULT_CONFIG, Path(temporary) / "other-fields")
            self.assertNotEqual(json.loads(second.read_text())["dataset_id"], prepared["dataset_id"])
            load_evidence.cache_clear()


if __name__ == "__main__":
    unittest.main()

"""Validate complete local OSM geometry, routing references, and frozen download publication."""

import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

try:
    import osmium
except ImportError:
    osmium = None
else:
    from runmap.sources.pbf import extract_region
from rasterio.warp import transform, transform_geom
from shapely.geometry import shape

from runmap.sources.geofabrik import download_finland, download_payload
from runmap.paths.graph import build_routing_graph
from runmap.cli import _input_provenance, _wgs84_bounds


def write_fixture(directory: Path) -> Path:
    assert osmium is not None
    coordinates = [(372000, 6672000), (376000, 6672000), (376000, 6676000), (372000, 6676000),
                   (374100, 6674100), (374300, 6674100), (374300, 6674300), (374100, 6674300),
                   (373900, 6674200), (374500, 6674200)]
    lon, lat = transform("EPSG:3067", "EPSG:4326", [p[0] for p in coordinates], [p[1] for p in coordinates])
    header = osmium.io.Header()
    header.add_box(osmium.osm.Box(23, 59, 27, 63))
    header.set("osmosis_replication_timestamp", "2026-11-03T21:24:23Z")
    source = directory / "fixture.osm.pbf"
    with osmium.SimpleWriter(source, header=header) as writer:
        for index, (x, y) in enumerate(zip(lon, lat), 1):
            tags = {"barrier": "gate", "foot": "yes"} if index == 9 else {}
            writer.add_node(osmium.osm.mutable.Node(id=index, location=(x, y), tags=tags))
        for identifier, refs, tags in [(11, [1, 2, 3], {}), (12, [3, 4, 1], {}),
                (13, [5, 6, 7, 8, 5], {}),
                (14, [9, 10], {"highway": "footway", "surface": "gravel", "oneway:foot": "yes", "lit": "yes"})]:
            writer.add_way(osmium.osm.mutable.Way(id=identifier, nodes=refs, tags=tags))
        writer.add_relation(osmium.osm.mutable.Relation(id=21,
            members=[("w", 11, "outer"), ("w", 12, "outer"), ("w", 13, "inner")],
            tags={"type": "multipolygon", "natural": "wood", "name": "Enclosing woodland"}))
    return source


class PbfImportTests(unittest.TestCase):
    @unittest.skipUnless(osmium is not None, "Requires the osm extra")
    def test_enclosing_multipolygon_holes_crossing_ways_and_node_tags(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_fixture(root)
            config = root / "config.json"
            request = json.loads(Path("config/environment/helsinki-capital-area.json").read_text())
            request["bounds_epsg3067"] = [374000, 6674000, 374400, 6674400]
            config.write_text(json.dumps(request))
            output = root / "regional"
            extract_region(source, config, output)
            feature = json.loads((output / "environment/features.geojson").read_text())["features"][0]
            self.assertEqual(feature["properties"]["osm_type"], "relation")
            self.assertEqual(feature["properties"]["osm_id"], 21)
            geometry = shape(transform_geom("EPSG:4326", "EPSG:3067", feature["geometry"]))
            self.assertEqual(len(geometry.geoms[0].interiors), 1)
            self.assertLess(geometry.bounds[0], 373000)
            network = json.loads((output / "network.json").read_text())
            nodes = {item["id"]: item for item in network["elements"] if item["type"] == "node"}
            self.assertEqual(set(nodes), {9, 10})
            self.assertEqual(nodes[9]["tags"]["barrier"], "gate")
            graph = build_routing_graph(network)
            self.assertEqual(len(graph["segments"]), 1)
            self.assertEqual(len(graph["arcs"]), 1)
            self.assertEqual(graph["segments"][0]["tags"]["surface"], "gravel")
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["osm_base_timestamp"], "2026-11-03T21:24:23Z")
            self.assertEqual(manifest["geometry_diagnostics"]["unassembled_tagged_multipolygons_in_source"], 0)
            self.assertFalse(list(output.glob("pbf-index-*")))
            bounds = _wgs84_bounds((374000, 6674000, 374400, 6674400))
            provenance = _input_provenance(output / "network.json", bounds)
            self.assertEqual(provenance["pbf_sha256"], manifest["pbf"]["sha256"])
            self.assertEqual(provenance["osm_base_timestamp"], manifest["osm_base_timestamp"])
            with self.assertRaises(ValueError):
                _input_provenance(output / "network.json", _wgs84_bounds((374000, 6674000, 374800, 6674800)))
            with (output / "network.json").open("a") as stream:
                stream.write(" ")
            with self.assertRaises(ValueError):
                _input_provenance(output / "network.json", bounds)
            with self.assertRaises(FileExistsError):
                extract_region(source, config, output)

    @unittest.skipUnless(osmium is not None, "Requires the osm extra")
    def test_region_outside_source_coverage_is_not_published(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_fixture(root)
            config = root / "config.json"
            request = json.loads(Path("config/environment/helsinki-capital-area.json").read_text())
            request["bounds_epsg3067"] = [500000, 7400000, 501000, 7401000]
            config.write_text(json.dumps(request))
            with self.assertRaises(ValueError):
                extract_region(source, config, root / "regional")
            self.assertFalse((root / "regional").exists())

    def test_download_requires_provider_checksum_and_refuses_completed_replacement(self) -> None:
        payload = b"frozen source"
        record = {"requested_url": "https://example.test/latest", "resolved_url": "https://example.test/dated.pbf",
                  "provider_md5": hashlib.md5(payload, usedforsecurity=False).hexdigest()}
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "snapshot"
            root.mkdir()
            (root / "download.json").write_text(json.dumps(record))
            def fetch(url: str, path: Path) -> None:
                path.write_bytes(b"wrong")
            with patch("runmap.sources.geofabrik.download_payload", side_effect=fetch):
                with self.assertRaises(ValueError):
                    download_finland(root, record["requested_url"])
            self.assertFalse((root / "manifest.json").exists())
            self.assertFalse((root / "finland.osm.pbf").exists())
            (root / "finland.osm.pbf.part").write_bytes(payload)
            with patch("runmap.sources.geofabrik.download_payload"):
                target = download_finland(root, record["requested_url"])
            manifest = json.loads((root / "manifest.json").read_text())
            self.assertEqual(manifest["pbf"]["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(target.read_bytes(), payload)
            with self.assertRaises(FileExistsError):
                download_finland(root, record["requested_url"])

    def test_download_resumes_or_restarts_when_range_is_ignored(self) -> None:
        class DownloadResponse(BytesIO):
            def __init__(self, payload: bytes, status: int, content_range: str) -> None:
                super().__init__(payload)
                self.status = status
                self.headers = {"Content-Range": content_range}

        with TemporaryDirectory() as temporary:
            partial = Path(temporary) / "source.part"
            for status, payload, expected in [(206, b"def", b"abcdef"), (200, b"abcdef", b"abcdef")]:
                partial.write_bytes(b"abc")
                response = DownloadResponse(payload, status, "bytes 3-5/6")
                with patch("runmap.sources.geofabrik.urlopen", return_value=response) as fetch:
                    download_payload("https://example.test/source.pbf", partial)
                    self.assertEqual(fetch.call_args.args[0].headers["Range"], "bytes=3-")
                self.assertEqual(partial.read_bytes(), expected)
            partial.write_bytes(b"abc")
            with patch("runmap.sources.geofabrik.urlopen", return_value=DownloadResponse(b"wrong", 206, "bytes 4-8/9")):
                with self.assertRaises(ValueError):
                    download_payload("https://example.test/source.pbf", partial)
            self.assertEqual(partial.read_bytes(), b"abc")


if __name__ == "__main__":
    unittest.main()

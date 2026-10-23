"""Native line encoding preserves vector-tile geometry and property contracts."""

import math
import unittest

import mapbox_vector_tile
from shapely.geometry import LineString, MultiLineString

from runmap import _routing_native
from runmap.api.path_tiles import Property


class NativeTileTests(unittest.TestCase):
    def test_linework_and_typed_properties_match_reference_encoder(self) -> None:
        geometries = [[[(0.0, 0.0), (0.0, 0.0), (12.0, 20.0), (-10.0, -5.0)]],
                      [[(4096.0, 4096.0), (4200.0, 10.0)], [(-32.0, 0.0), (0.0, 2.0)]],
                      [[(5.0, 5.0), (5.0, 5.0)]]]
        properties: dict[str, Property] = {"segment_id": "Mäkipolku", "eligible": False, "stairs": True,
                                          "distance_from_start_m": 125, "grade_percent": 1.5, "negative": -12}
        native = _routing_native.encode_line_tile([(lines, properties) for lines in geometries], 4096)
        features = [{"geometry": LineString(lines[0]) if len(lines) == 1 else MultiLineString(lines),
                     "properties": properties} for lines in geometries]
        reference = mapbox_vector_tile.encode({"name": "paths", "features": features},
            default_options={"y_coord_down": True, "extents": 4096})
        self.assertEqual(mapbox_vector_tile.decode(native), mapbox_vector_tile.decode(reference))
        decoded = mapbox_vector_tile.decode(native)["paths"]["features"]
        self.assertEqual(len(decoded), 2)
        self.assertIs(decoded[0]["properties"]["eligible"], False)
        self.assertIs(decoded[0]["properties"]["stairs"], True)

    def test_empty_layer_and_invalid_inputs(self) -> None:
        self.assertEqual(mapbox_vector_tile.decode(_routing_native.encode_line_tile([], 4096))["paths"]["features"], [])
        for coordinate in (math.nan, math.inf, 1e30):
            with self.assertRaises(ValueError):
                _routing_native.encode_line_tile([([[(coordinate, 0.0), (1.0, 1.0)]], {})], 4096)
        with self.assertRaises(ValueError):
            _routing_native.encode_line_tile([], 0)
        with self.assertRaises(ValueError):
            _routing_native.encode_line_tile([([[(0.0, 0.0), (1.0, 1.0)]], {"bad": math.nan})], 4096)

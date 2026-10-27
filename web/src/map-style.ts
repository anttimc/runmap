/** Define the neutral basemap used beneath terrain and routing overlays. */
import type { StyleSpecification } from "maplibre-gl";

export const FIRST_LABEL_LAYER_ID = "water-place-labels";
export const FIRST_ABOVE_TERRAIN_LAYER_ID = "context-road-casing";
const RUNNER_PATH_CASING_LAYER_ID = "runner-path-casing";

export function openStreetMapStyle(): StyleSpecification {
  const tileJsonUrl =
    (import.meta.env.VITE_OSM_VECTOR_TILEJSON_URL as string | undefined) ||
    "https://vector.openstreetmap.org/shortbread_v1/tilejson.json";
  const labelFont = ["system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"];
  return {
    version: 8,
    name: "Runmap neutral OSM",
    sources: {
      openstreetmap: {
        type: "vector",
        url: tileJsonUrl,
        attribution:
          '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>',
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#eceae5" } },
      {
        id: "land",
        type: "fill",
        source: "openstreetmap",
        "source-layer": "land",
        paint: {
          "fill-color": [
            "match",
            ["get", "kind"],
            ["forest", "wood"],
            "#deded8",
            ["grass", "meadow", "park"],
            "#e5e4de",
            "#e9e7e2",
          ],
          "fill-opacity": 0.78,
        },
      },
      {
        id: "sites",
        type: "fill",
        source: "openstreetmap",
        "source-layer": "sites",
        paint: { "fill-color": "#dedbd5", "fill-opacity": 0.55 },
      },
      {
        id: "ocean",
        type: "fill",
        source: "openstreetmap",
        "source-layer": "ocean",
        paint: { "fill-color": "#d7dde0" },
      },
      {
        id: "water",
        type: "fill",
        source: "openstreetmap",
        "source-layer": "water_polygons",
        paint: { "fill-color": "#d7dde0" },
      },
      {
        id: "street-areas",
        type: "fill",
        source: "openstreetmap",
        "source-layer": "street_polygons",
        paint: { "fill-color": "#d2d0ca", "fill-opacity": 0.8 },
      },
      {
        id: "buildings",
        type: "fill",
        source: "openstreetmap",
        "source-layer": "buildings",
        minzoom: 13,
        paint: {
          "fill-color": "#cfccc6",
          "fill-opacity": 0.7,
          "fill-outline-color": "#bbb9b4",
        },
      },
      {
        id: "water-lines",
        type: "line",
        source: "openstreetmap",
        "source-layer": "water_lines",
        paint: {
          "line-color": "#aeb9bf",
          "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.5, 16, 1.8],
        },
      },
      {
        id: "road-casing",
        type: "line",
        source: "openstreetmap",
        "source-layer": "streets",
        filter: [
          "all",
          ["!", ["get", "rail"]],
          [
            "in",
            ["get", "kind"],
            ["literal", ["motorway", "trunk", "primary", "secondary", "tertiary"]],
          ],
        ],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#a5a49f",
          "line-width": ["interpolate", ["linear"], ["zoom"], 7, 1, 12, 3, 16, 8],
          "line-opacity": 0.8,
        },
      },
      {
        id: "roads",
        type: "line",
        source: "openstreetmap",
        "source-layer": "streets",
        filter: [
          "all",
          ["!", ["get", "rail"]],
          [
            "!",
            [
              "in",
              ["get", "kind"],
              ["literal", ["pedestrian", "track", "path", "footway", "cycleway", "steps"]],
            ],
          ],
        ],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": [
            "match",
            ["get", "kind"],
            ["motorway", "trunk", "primary", "secondary"],
            "#f4f2ed",
            "#d8d6d0",
          ],
          "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.6, 12, 1.8, 16, 5.5],
          "line-opacity": 0.92,
        },
      },
      {
        id: "railways",
        type: "line",
        source: "openstreetmap",
        "source-layer": "streets",
        filter: ["==", ["get", "rail"], true],
        paint: {
          "line-color": "#858580",
          "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.6, 16, 1.8],
          "line-dasharray": [3, 2],
          "line-opacity": 0.75,
        },
      },
      {
        id: FIRST_ABOVE_TERRAIN_LAYER_ID,
        type: "line",
        source: "openstreetmap",
        "source-layer": "streets",
        filter: [
          "all",
          ["!", ["get", "rail"]],
          [
            "in",
            ["get", "kind"],
            [
              "literal",
              [
                "motorway",
                "trunk",
                "primary",
                "secondary",
                "tertiary",
                "unclassified",
                "residential",
                "living_street",
                "service",
              ],
            ],
          ],
        ],
        minzoom: 9,
        maxzoom: 13.5,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#4e504d",
          "line-width": ["interpolate", ["linear"], ["zoom"], 9, 1.2, 12, 2.5, 13.5, 3.2],
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 9, 0.38, 12, 0.48, 13.5, 0],
        },
      },
      {
        id: "context-roads",
        type: "line",
        source: "openstreetmap",
        "source-layer": "streets",
        filter: [
          "all",
          ["!", ["get", "rail"]],
          [
            "in",
            ["get", "kind"],
            [
              "literal",
              [
                "motorway",
                "trunk",
                "primary",
                "secondary",
                "tertiary",
                "unclassified",
                "residential",
                "living_street",
                "service",
              ],
            ],
          ],
        ],
        minzoom: 9,
        maxzoom: 13.5,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#f8f7f3",
          "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.5, 12, 1.3, 13.5, 1.7],
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 9, 0.8, 12, 0.88, 13.5, 0],
        },
      },
      {
        id: RUNNER_PATH_CASING_LAYER_ID,
        type: "line",
        source: "openstreetmap",
        "source-layer": "streets",
        filter: [
          "all",
          [
            "in",
            ["get", "kind"],
            ["literal", ["pedestrian", "track", "path", "footway", "cycleway", "steps"]],
          ],
          ["match", ["get", "foot"], "no", false, true],
        ],
        minzoom: 13,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "rgba(250, 249, 245, 0.92)",
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            13,
            ["match", ["get", "kind"], "pedestrian", 4.2, 2.3],
            17,
            ["match", ["get", "kind"], "pedestrian", 6.4, 3.8],
          ],
        },
      },
      {
        id: "runner-paths-unmaintained",
        type: "line",
        source: "openstreetmap",
        "source-layer": "streets",
        filter: [
          "all",
          ["in", ["get", "kind"], ["literal", ["track", "path", "footway", "cycleway", "steps"]]],
          ["match", ["get", "foot"], "no", false, true],
          [
            "!",
            [
              "any",
              [
                "match",
                ["get", "surface"],
                [
                  "asphalt",
                  "concrete",
                  "concrete:plates",
                  "paved",
                  "paving_stones",
                  "sett",
                  "cobblestone",
                  "compacted",
                  "fine_gravel",
                  "gravel",
                  "wood",
                ],
                true,
                false,
              ],
              ["match", ["get", "tracktype"], ["grade1", "grade2"], true, false],
              [
                "all",
                ["in", ["get", "kind"], ["literal", ["footway", "cycleway", "track"]]],
                [
                  "!",
                  [
                    "any",
                    [
                      "match",
                      ["get", "surface"],
                      ["dirt", "earth", "ground", "grass", "mud", "sand"],
                      true,
                      false,
                    ],
                    ["match", ["get", "tracktype"], ["grade3", "grade4", "grade5"], true, false],
                  ],
                ],
              ],
            ],
          ],
        ],
        minzoom: 13,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#555752",
          "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.7, 17, 1.15],
          "line-dasharray": [1, 1.8],
          "line-opacity": 0.9,
        },
      },
      {
        id: "runner-paths-maintained",
        type: "line",
        source: "openstreetmap",
        "source-layer": "streets",
        filter: [
          "all",
          ["in", ["get", "kind"], ["literal", ["track", "path", "footway", "cycleway", "steps"]]],
          ["match", ["get", "foot"], "no", false, true],
          [
            "any",
            [
              "match",
              ["get", "surface"],
              [
                "asphalt",
                "concrete",
                "concrete:plates",
                "paved",
                "paving_stones",
                "sett",
                "cobblestone",
                "compacted",
                "fine_gravel",
                "gravel",
                "wood",
              ],
              true,
              false,
            ],
            ["match", ["get", "tracktype"], ["grade1", "grade2"], true, false],
            [
              "all",
              ["in", ["get", "kind"], ["literal", ["footway", "cycleway", "track"]]],
              [
                "!",
                [
                  "any",
                  [
                    "match",
                    ["get", "surface"],
                    ["dirt", "earth", "ground", "grass", "mud", "sand"],
                    true,
                    false,
                  ],
                  ["match", ["get", "tracktype"], ["grade3", "grade4", "grade5"], true, false],
                ],
              ],
            ],
          ],
        ],
        minzoom: 13,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#292a28",
          "line-width": ["interpolate", ["linear"], ["zoom"], 13, 1.05, 17, 1.75],
          "line-dasharray": [3, 1.5],
          "line-opacity": 0.96,
        },
      },
      {
        id: "runner-pedestrian-roads",
        type: "line",
        source: "openstreetmap",
        "source-layer": "streets",
        filter: [
          "all",
          ["==", ["get", "kind"], "pedestrian"],
          ["match", ["get", "foot"], "no", false, true],
        ],
        minzoom: 13,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#292a28",
          "line-width": ["interpolate", ["linear"], ["zoom"], 13, 2.1, 17, 3.3],
          "line-dasharray": [3, 1.2],
          "line-opacity": 0.96,
        },
      },
      {
        id: FIRST_LABEL_LAYER_ID,
        type: "symbol",
        source: "openstreetmap",
        "source-layer": "water_polygons_labels",
        minzoom: 11,
        layout: {
          "text-field": ["get", "name"],
          "text-font": labelFont,
          "text-size": 11,
        },
        paint: {
          "text-color": "#50585c",
          "text-halo-color": "rgba(247, 246, 242, 0.94)",
          "text-halo-width": 1.2,
        },
      },
      {
        id: "street-labels",
        type: "symbol",
        source: "openstreetmap",
        "source-layer": "street_labels",
        minzoom: 12,
        layout: {
          "symbol-placement": "line",
          "text-field": ["get", "name"],
          "text-font": labelFont,
          "text-size": ["interpolate", ["linear"], ["zoom"], 12, 10, 16, 12],
          "text-letter-spacing": 0.02,
        },
        paint: {
          "text-color": "#3f403e",
          "text-halo-color": "rgba(247, 246, 242, 0.96)",
          "text-halo-width": 1.3,
        },
      },
      {
        id: "place-labels",
        type: "symbol",
        source: "openstreetmap",
        "source-layer": "place_labels",
        layout: {
          "text-field": ["get", "name"],
          "text-font": labelFont,
          "text-size": [
            "match",
            ["get", "kind"],
            ["city", "town"],
            15,
            ["village", "suburb"],
            13,
            11,
          ],
          "text-variable-anchor": ["top", "bottom", "left", "right"],
          "text-radial-offset": 0.35,
        },
        paint: {
          "text-color": "#343532",
          "text-halo-color": "rgba(247, 246, 242, 0.96)",
          "text-halo-width": 1.5,
        },
      },
    ],
  };
}

import {
  AttributionControl,
  ImageSource,
  Map,
  NavigationControl,
  RasterTileSource,
  setWorkerUrl,
  type MapSourceDataEvent,
  type StyleSpecification,
} from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { startLayout } from "./layout";
import "./style.css";
import { startPaths } from "./paths";

// MapLibre derives its worker URL from import.meta.url by default. After Vite
// bundles the library that resolves beside our app bundle, where the worker is
// not automatically copied. A worker-aware URL import makes Vite bundle the
// worker and its shared module, then gives MapLibre the production URL.
setWorkerUrl(maplibreWorkerUrl);

interface MapConfig {
  bounds_wgs84: [number, number, number, number];
  image_coordinates: [
    [number, number],
    [number, number],
    [number, number],
    [number, number],
  ];
  source_shape: [number, number];
  resolution_m: number;
  smoothing: {
    minimum: number;
    maximum: number;
    step: number;
    default: number;
    raw_value: number;
  };
  slope_legend: { percent: number; color: string; opacity: number }[];
  terrain_tiles?: {
    template: string;
    tile_size: number;
    min_zoom: number;
    max_zoom: number;
  };
  contour_tiles?: {
    template: string;
    tile_size: number;
    min_zoom: number;
    max_zoom: number;
    interval_m: number;
    index_interval_m: number;
  };
}

const FIRST_LABEL_LAYER_ID = "water-place-labels";
const FIRST_ABOVE_TERRAIN_LAYER_ID = "context-road-casing";
const RUNNER_PATH_CASING_LAYER_ID = "runner-path-casing";

function openStreetMapStyle(): StyleSpecification {
  const tileJsonUrl = (import.meta.env.VITE_OSM_VECTOR_TILEJSON_URL as string | undefined)
    || "https://vector.openstreetmap.org/shortbread_v1/tilejson.json";
  const labelFont = ["system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"];
  return {
    version: 8,
    name: "Runmap neutral OSM",
    sources: {
      openstreetmap: {
        type: "vector",
        url: tileJsonUrl,
        attribution: '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>',
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
            "match", ["get", "kind"],
            ["forest", "wood"], "#deded8",
            ["grass", "meadow", "park"], "#e5e4de",
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
        filter: ["all", ["!", ["get", "rail"]], ["in", ["get", "kind"], ["literal", ["motorway", "trunk", "primary", "secondary", "tertiary"]]]],
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
        filter: ["all", ["!", ["get", "rail"]], ["!", ["in", ["get", "kind"], ["literal", ["pedestrian", "track", "path", "footway", "cycleway", "steps"]]]]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": [
            "match", ["get", "kind"],
            ["motorway", "trunk", "primary", "secondary"], "#f4f2ed",
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
          ["in", ["get", "kind"], ["literal", ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service"]]],
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
          ["in", ["get", "kind"], ["literal", ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service"]]],
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
          ["in", ["get", "kind"], ["literal", ["pedestrian", "track", "path", "footway", "cycleway", "steps"]]],
          ["match", ["get", "foot"], "no", false, true],
        ],
        minzoom: 13,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "rgba(250, 249, 245, 0.92)",
          "line-width": [
            "interpolate", ["linear"], ["zoom"],
            13, ["match", ["get", "kind"], "pedestrian", 4.2, 2.3],
            17, ["match", ["get", "kind"], "pedestrian", 6.4, 3.8],
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
          ["!", ["any",
            ["match", ["get", "surface"], ["asphalt", "concrete", "concrete:plates", "paved", "paving_stones", "sett", "cobblestone", "compacted", "fine_gravel", "gravel", "wood"], true, false],
            ["match", ["get", "tracktype"], ["grade1", "grade2"], true, false],
            ["all",
              ["in", ["get", "kind"], ["literal", ["footway", "cycleway", "track"]]],
              ["!", ["any",
                ["match", ["get", "surface"], ["dirt", "earth", "ground", "grass", "mud", "sand"], true, false],
                ["match", ["get", "tracktype"], ["grade3", "grade4", "grade5"], true, false],
              ]],
            ],
          ]],
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
          ["any",
            ["match", ["get", "surface"], ["asphalt", "concrete", "concrete:plates", "paved", "paving_stones", "sett", "cobblestone", "compacted", "fine_gravel", "gravel", "wood"], true, false],
            ["match", ["get", "tracktype"], ["grade1", "grade2"], true, false],
            ["all",
              ["in", ["get", "kind"], ["literal", ["footway", "cycleway", "track"]]],
              ["!", ["any",
                ["match", ["get", "surface"], ["dirt", "earth", "ground", "grass", "mud", "sand"], true, false],
                ["match", ["get", "tracktype"], ["grade3", "grade4", "grade5"], true, false],
              ]],
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
            "match", ["get", "kind"],
            ["city", "town"], 15,
            ["village", "suburb"], 13,
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

const wavelengthInput = element<HTMLInputElement>("wavelength");
const wavelengthValue = element<HTMLOutputElement>("wavelength-value");
const opacityInput = element<HTMLInputElement>("opacity");
const opacityValue = element<HTMLOutputElement>("opacity-value");
const status = element<HTMLSpanElement>("status");
const legendBar = element<HTMLDivElement>("legend-bar");
const legendLabels = element<HTMLDivElement>("legend-labels");

let map: Map;
let debounceTimer: number | undefined;
let terrainTileTemplate: string | undefined;

function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing #${id}`);
  return found as T;
}

function terrainUrl(wavelength: number): string {
  return `/api/terrain.png?wavelength_m=${wavelength}&max_size=1600`;
}

function terrainTiles(wavelength: number): string[] {
  if (!terrainTileTemplate) return [];
  return [terrainTileTemplate.replace("{wavelength}", String(wavelength))];
}

function setStatus(message: string, kind: "ready" | "busy" | "error" = "ready"): void {
  status.textContent = message;
  status.dataset.kind = kind;
}

function renderLegend(stops: MapConfig["slope_legend"]): void {
  const colors = stops.map(stop => {
    const color = `color-mix(in srgb, ${stop.color} ${100 * stop.opacity}%, transparent)`;
    return `${color} ${100 * stop.percent / 40}%`;
  });
  legendBar.style.background = `linear-gradient(to right, ${colors.join(", ")})`;
  legendLabels.replaceChildren(
    ...stops
      .slice()
      .filter(stop => stop.percent === 0 || stop.percent >= 10)
      .reverse()
      .map((stop) => {
        const label = document.createElement("span");
        label.textContent = `${stop.percent}%`;
        label.style.position = "absolute";
        label.style.left = `${100 * stop.percent / 40}%`;
        label.style.transform = stop.percent === 0 ? "none" : stop.percent === 40 ? "translateX(-100%)" : "translateX(-50%)";
        return label;
      }),
  );
}

function updateTerrain(wavelength: number): void {
  setStatus("Updating…", "busy");
  const source = map.getSource("terrain");
  if (terrainTileTemplate) {
    (source as RasterTileSource | undefined)?.setTiles(terrainTiles(wavelength));
  } else {
    (source as ImageSource | undefined)?.updateImage({ url: terrainUrl(wavelength) });
  }
}

function scheduleTerrainUpdate(): void {
  const wavelength = Number(wavelengthInput.value);
  wavelengthValue.value = wavelength === 0 ? "Raw" : `${wavelength} m`;
  window.clearTimeout(debounceTimer);
  debounceTimer = window.setTimeout(() => updateTerrain(wavelength), 180);
}

async function start(): Promise<void> {
  startLayout();
  const response = await fetch("/api/config");
  if (!response.ok) throw new Error(`Configuration request failed (${response.status})`);
  const config = (await response.json()) as MapConfig;
  terrainTileTemplate = config.terrain_tiles?.template;
  const setting = config.smoothing;
  wavelengthInput.min = String(setting.minimum);
  wavelengthInput.max = String(setting.maximum);
  wavelengthInput.step = String(setting.step);
  wavelengthInput.value = String(setting.default);
  wavelengthValue.value = `${setting.default} m`;
  renderLegend(config.slope_legend);

  const styleUrl = import.meta.env.VITE_MAP_STYLE_URL as string | undefined;
  map = new Map({
    container: "map",
    style: styleUrl || openStreetMapStyle(),
    center: [
      (config.bounds_wgs84[0] + config.bounds_wgs84[2]) / 2,
      (config.bounds_wgs84[1] + config.bounds_wgs84[3]) / 2,
    ],
    attributionControl: false,
  });
  map.addControl(new NavigationControl({ visualizePitch: true }), "bottom-right");
  map.addControl(new AttributionControl({
    compact: true,
    customAttribution: "Elevation: National Land Survey of Finland (CC BY 4.0)",
  }));
  map.on("sourcedata", (event: MapSourceDataEvent) => {
    if (event.sourceId === "terrain" && event.isSourceLoaded) setStatus("Ready");
  });

  map.on("load", () => {
    if (config.terrain_tiles) {
      map.addSource("terrain", {
        type: "raster",
        tiles: terrainTiles(setting.default),
        tileSize: config.terrain_tiles.tile_size,
        minzoom: config.terrain_tiles.min_zoom,
        maxzoom: config.terrain_tiles.max_zoom,
        bounds: config.bounds_wgs84,
        attribution: "National Land Survey of Finland (CC BY 4.0)",
      });
    } else {
      map.addSource("terrain", {
        type: "image",
        url: terrainUrl(setting.default),
        coordinates: config.image_coordinates,
      });
    }
    map.addLayer({
      id: "terrain-slope",
      type: "raster",
      source: "terrain",
      paint: {
        "raster-opacity": Number(opacityInput.value) / 100,
        "raster-fade-duration": 0,
        "raster-resampling": "linear",
      },
    }, map.getLayer(FIRST_ABOVE_TERRAIN_LAYER_ID)
      ? FIRST_ABOVE_TERRAIN_LAYER_ID
      : map.getLayer(FIRST_LABEL_LAYER_ID)
        ? FIRST_LABEL_LAYER_ID
        : undefined);
    if (config.contour_tiles) {
      map.addSource("contours", {
        type: "vector",
        tiles: [config.contour_tiles.template],
        minzoom: config.contour_tiles.min_zoom,
        maxzoom: config.contour_tiles.max_zoom,
        bounds: config.bounds_wgs84,
        attribution: "National Land Survey of Finland (CC BY 4.0)",
      });
      map.addLayer({
        id: "elevation-contours-minor",
        type: "line",
        source: "contours",
        "source-layer": "contours",
        minzoom: config.contour_tiles.min_zoom,
        filter: ["==", ["get", "index"], false],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#303432",
          "line-opacity": 0.38,
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.45, 15, 0.9],
        },
      }, map.getLayer(FIRST_ABOVE_TERRAIN_LAYER_ID)
        ? FIRST_ABOVE_TERRAIN_LAYER_ID
        : map.getLayer(FIRST_LABEL_LAYER_ID)
          ? FIRST_LABEL_LAYER_ID
          : undefined);
      map.addLayer({
        id: "elevation-contours-index",
        type: "line",
        source: "contours",
        "source-layer": "contours",
        minzoom: config.contour_tiles.min_zoom,
        filter: ["==", ["get", "index"], true],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#303432",
          "line-opacity": 0.62,
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.8, 15, 1.5],
        },
      }, map.getLayer(FIRST_ABOVE_TERRAIN_LAYER_ID)
        ? FIRST_ABOVE_TERRAIN_LAYER_ID
        : map.getLayer(FIRST_LABEL_LAYER_ID)
          ? FIRST_LABEL_LAYER_ID
          : undefined);
    }
    map.fitBounds(
      [[config.bounds_wgs84[0], config.bounds_wgs84[1]], [config.bounds_wgs84[2], config.bounds_wgs84[3]]],
      { padding: 42, duration: 0 },
    );
    wavelengthInput.disabled = false;
    opacityInput.disabled = false;
    setStatus("Loading overlay…", "busy");
    void startPaths(map, config.slope_legend);
  });

  wavelengthInput.addEventListener("input", scheduleTerrainUpdate);
  wavelengthInput.addEventListener("change", () => {
    window.clearTimeout(debounceTimer);
    updateTerrain(Number(wavelengthInput.value));
  });
  element<HTMLInputElement>("contours-visible").addEventListener("change", event => {
    const visible = (event.target as HTMLInputElement).checked;
    for (const id of ["elevation-contours-minor", "elevation-contours-index"]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
    }
  });
  opacityInput.addEventListener("input", () => {
    const opacity = Number(opacityInput.value);
    opacityValue.value = `${opacity}%`;
    if (map.getLayer("terrain-slope")) {
      map.setPaintProperty("terrain-slope", "raster-opacity", opacity / 100);
    }
  });
}

void start().catch((error) => {
  console.error(error);
  setStatus("Startup failed", "error");
});

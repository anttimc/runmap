/** Initialize the map, terrain layers, and application feature controllers. */
import {
  AttributionControl,
  Map,
  NavigationControl,
  setWorkerUrl,
  type MapSourceDataEvent,
} from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { startLayout } from "./layout";
import "./style.css";
import { startMapTools } from "./app";
import { loadMapConfig } from "./api";
import type { MapConfig } from "./api-types";
import { element } from "./dom";
import {
  FIRST_LABEL_LAYER_ID,
  FIRST_ABOVE_TERRAIN_LAYER_ID,
  openStreetMapStyle,
} from "./map-style";

// MapLibre derives its worker URL from import.meta.url by default. After Vite
// bundles the library that resolves beside our app bundle, where the worker is
// not automatically copied. A worker-aware URL import makes Vite bundle the
// worker and its shared module, then gives MapLibre the production URL.
setWorkerUrl(maplibreWorkerUrl);

const backgroundSmoothingM = 40;
const opacityInput = element<HTMLInputElement>("opacity");
const opacityValue = element<HTMLOutputElement>("opacity-value");
const backgroundLayer = element<HTMLInputElement>("layer-background");
const distanceLayer = element<HTMLInputElement>("layer-distance");
const status = element<HTMLSpanElement>("status");
const legendBar = element<HTMLDivElement>("legend-bar");
const legendLabels = element<HTMLDivElement>("legend-labels");

let map: Map;

function setStatus(message: string, kind: "ready" | "busy" | "error" = "ready"): void {
  status.textContent = message;
  status.dataset.kind = kind;
}

function renderLegend(stops: MapConfig["slope_legend"]): void {
  const colors = stops.map((stop) => {
    const color = `color-mix(in srgb, ${stop.color} ${100 * stop.opacity}%, transparent)`;
    return `${color} ${(100 * stop.percent) / 40}%`;
  });
  legendBar.style.background = `linear-gradient(to right, ${colors.join(", ")})`;
  legendLabels.replaceChildren(
    ...stops
      .slice()
      .filter((stop) => stop.percent === 0 || stop.percent >= 10)
      .reverse()
      .map((stop) => {
        const label = document.createElement("span");
        label.textContent = `${stop.percent}%`;
        label.style.position = "absolute";
        label.style.left = `${(100 * stop.percent) / 40}%`;
        label.style.transform =
          stop.percent === 0
            ? "none"
            : stop.percent === 40
              ? "translateX(-100%)"
              : "translateX(-50%)";
        return label;
      }),
  );
}

function updateBackground(): void {
  const opacity = Number(opacityInput.value);
  const visible = backgroundLayer.checked && opacity > 0;
  opacityValue.value = `${opacity}%`;
  element("terrain-legend").hidden = !visible;
  if (map?.getLayer("terrain-slope")) {
    map.setLayoutProperty("terrain-slope", "visibility", visible ? "visible" : "none");
    map.setPaintProperty(
      "terrain-slope",
      "raster-opacity",
      (opacity / 100) * (distanceLayer.checked ? 0.3 : 1),
    );
  }
}

async function start(): Promise<void> {
  startLayout();
  const config = await loadMapConfig();
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
  map.addControl(
    new AttributionControl({
      compact: true,
      customAttribution: "Elevation: National Land Survey of Finland (CC BY 4.0)",
    }),
  );
  map.on("sourcedata", (event: MapSourceDataEvent) => {
    if (event.sourceId === "terrain" && event.isSourceLoaded) setStatus("Ready");
  });

  map.on("load", () => {
    if (config.terrain_tiles) {
      map.addSource("terrain", {
        type: "raster",
        tiles: [
          config.terrain_tiles.template.replace("{wavelength}", String(backgroundSmoothingM)),
        ],
        tileSize: config.terrain_tiles.tile_size,
        minzoom: config.terrain_tiles.min_zoom,
        maxzoom: config.terrain_tiles.max_zoom,
        bounds: config.bounds_wgs84,
        attribution: "National Land Survey of Finland (CC BY 4.0)",
      });
    } else {
      map.addSource("terrain", {
        type: "image",
        url: `/api/terrain.png?wavelength_m=${backgroundSmoothingM}&max_size=1600`,
        coordinates: config.image_coordinates,
      });
    }
    map.addLayer(
      {
        id: "terrain-slope",
        type: "raster",
        source: "terrain",
        layout: { visibility: "none" },
        paint: {
          "raster-opacity": Number(opacityInput.value) / 100,
          "raster-fade-duration": 0,
          "raster-resampling": "linear",
        },
      },
      map.getLayer(FIRST_ABOVE_TERRAIN_LAYER_ID)
        ? FIRST_ABOVE_TERRAIN_LAYER_ID
        : map.getLayer(FIRST_LABEL_LAYER_ID)
          ? FIRST_LABEL_LAYER_ID
          : undefined,
    );
    if (config.contour_tiles) {
      map.addSource("contours", {
        type: "vector",
        tiles: [config.contour_tiles.template],
        minzoom: config.contour_tiles.min_zoom,
        maxzoom: config.contour_tiles.max_zoom,
        bounds: config.bounds_wgs84,
        attribution: "National Land Survey of Finland (CC BY 4.0)",
      });
      map.addLayer(
        {
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
        },
        map.getLayer(FIRST_ABOVE_TERRAIN_LAYER_ID)
          ? FIRST_ABOVE_TERRAIN_LAYER_ID
          : map.getLayer(FIRST_LABEL_LAYER_ID)
            ? FIRST_LABEL_LAYER_ID
            : undefined,
      );
      map.addLayer(
        {
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
        },
        map.getLayer(FIRST_ABOVE_TERRAIN_LAYER_ID)
          ? FIRST_ABOVE_TERRAIN_LAYER_ID
          : map.getLayer(FIRST_LABEL_LAYER_ID)
            ? FIRST_LABEL_LAYER_ID
            : undefined,
      );
    }
    map.fitBounds(
      [
        [config.bounds_wgs84[0], config.bounds_wgs84[1]],
        [config.bounds_wgs84[2], config.bounds_wgs84[3]],
      ],
      { padding: 42, duration: 0 },
    );
    opacityInput.disabled = false;
    backgroundLayer.disabled = false;
    updateBackground();
    setStatus("Loading overlay…", "busy");
    void startMapTools(map, config.slope_legend);
  });

  element<HTMLInputElement>("contours-visible").addEventListener("change", (event) => {
    const visible = (event.target as HTMLInputElement).checked;
    for (const id of ["elevation-contours-minor", "elevation-contours-index"]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
    }
  });
  opacityInput.addEventListener("input", updateBackground);
  backgroundLayer.addEventListener("change", updateBackground);
  distanceLayer.addEventListener("change", updateBackground);
  updateBackground();
}

void start().catch((error) => {
  console.error(error);
  setStatus("Startup failed", "error");
});

/** Add independently controlled tiled environment fields below roads and routes. */
import type { Map } from "maplibre-gl";
import { element } from "./dom.ts";
import { FIRST_ABOVE_TERRAIN_LAYER_ID, FIRST_LABEL_LAYER_ID } from "./map-style.ts";

interface EnvironmentConfig {
  dataset_id: string;
  tile_template: string;
  tile_size: number;
  min_zoom: number;
  max_zoom: number;
  bounds_wgs84: [number, number, number, number];
}

const fields = [
  { id: "greenery", label: "Greenery", color: "#1e8a40" },
  { id: "water", label: "Water proximity", color: "#1478e0" },
  { id: "traffic", label: "Traffic exposure", color: "#dc3720" },
  { id: "wetland", label: "Wetland presence", color: "#8b44af" },
  { id: "support", label: "Coverage", color: "#d68d0c" },
];

function addControls(map: Map, config: EnvironmentConfig): void {
  const controls = element("environment-layer-controls");
  const master = element<HTMLInputElement>("layer-environment");
  const toggles: HTMLInputElement[] = [];
  for (const field of fields) {
    const id = `environment-field-${field.id}`;
    map.addSource(id, {
      type: "raster",
      tiles: [config.tile_template.replace("{layer}", field.id)],
      tileSize: config.tile_size,
      minzoom: config.min_zoom,
      maxzoom: config.max_zoom,
      bounds: config.bounds_wgs84,
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> (ODbL)',
    });
    map.addLayer(
      {
        id,
        type: "raster",
        source: id,
        layout: { visibility: "none" },
        paint: { "raster-opacity": 0.5, "raster-fade-duration": 0, "raster-resampling": "nearest" },
      },
      map.getLayer(FIRST_ABOVE_TERRAIN_LAYER_ID)
        ? FIRST_ABOVE_TERRAIN_LAYER_ID
        : map.getLayer(FIRST_LABEL_LAYER_ID)
          ? FIRST_LABEL_LAYER_ID
          : undefined,
    );
    const row = document.createElement("div");
    row.className = "environment-field-control";
    const label = document.createElement("label");
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.id = `layer-environment-${field.id}`;
    toggle.checked = field.id === "greenery";
    toggle.addEventListener("change", () => {
      map.setLayoutProperty(
        id,
        "visibility",
        master.checked && toggle.checked ? "visible" : "none",
      );
      opacity.disabled = !toggle.checked;
    });
    toggles.push(toggle);
    const swatch = document.createElement("i");
    swatch.className = "environment-field-swatch";
    swatch.style.background =
      field.id === "support"
        ? "repeating-linear-gradient(135deg, #858585 0 3px, #d68d0c 3px 6px)"
        : field.color;
    label.append(toggle, swatch, document.createTextNode(field.label));
    const opacity = document.createElement("input");
    opacity.type = "range";
    opacity.min = "0";
    opacity.max = "100";
    opacity.value = "50";
    opacity.disabled = !toggle.checked;
    opacity.setAttribute("aria-label", `${field.label} opacity`);
    const value = document.createElement("output");
    value.textContent = "50%";
    opacity.addEventListener("input", () => {
      map.setPaintProperty(id, "raster-opacity", Number(opacity.value) / 100);
      value.textContent = `${opacity.value}%`;
    });
    row.append(label, opacity, value);
    controls.append(row);
  }
  master.disabled = false;
  master.addEventListener("change", () => {
    for (const toggle of toggles) {
      const id = `environment-field-${toggle.id.replace("layer-environment-", "")}`;
      map.setLayoutProperty(
        id,
        "visibility",
        master.checked && toggle.checked ? "visible" : "none",
      );
    }
  });
}

export async function startEnvironmentLayers(map: Map): Promise<void> {
  const status = element("environment-layer-status");
  try {
    const response = await fetch("/api/environment/tiles/config", { cache: "no-store" });
    if (!response.ok)
      throw new Error(
        response.status === 404
          ? "Environment layers have not been prepared for this server."
          : "Environment layers could not load. Reload to retry.",
      );
    const config = (await response.json()) as EnvironmentConfig;
    addControls(map, config);
    status.textContent = "10 m resolution";
    map.on("error", (event) => {
      if ("sourceId" in event && String(event.sourceId).startsWith("environment-field-"))
        status.textContent = "Environment tile failed. Reload to retry.";
    });
  } catch (error: unknown) {
    status.textContent = error instanceof Error ? error.message : "Environment layers unavailable.";
  }
}

/** Inspect source signal evidence independently of the routing event model. */
import type { GeoJSONSource, Map, MapLayerMouseEvent } from "maplibre-gl";
import type { FeatureCollection } from "geojson";
import { element } from "./dom.ts";

const SOURCE = "traffic-light-evidence";
const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

interface SignalMapConfig {
  dataset_id: string;
  url: string;
  minimum_zoom: number;
}

export function signalDetails(properties: Record<string, unknown>): string {
  const kind =
    properties.kind === "pedestrian" ? "Mapped pedestrian signal" : "Other or uncertain signal";
  let tags: Record<string, unknown> = {};
  try {
    const value: unknown =
      typeof properties.tags === "string" ? JSON.parse(properties.tags) : properties.tags;
    if (value && typeof value === "object" && !Array.isArray(value))
      tags = value as Record<string, unknown>;
  } catch {
    /* Missing details do not change the source marker classification. */
  }
  const evidence = [
    "highway",
    "crossing",
    "crossing:signals",
    "railway",
    "traffic_signals",
    "traffic_signals:direction",
  ]
    .filter((key) => typeof tags[key] === "string")
    .map((key) => `${key}=${tags[key]}`);
  return `${kind} · OSM node ${properties.osm_id}. ${evidence.join("; ")}.`;
}

function signalControl(): HTMLInputElement {
  const label = document.createElement("label");
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.id = "layer-environment-signals";
  toggle.checked = false;
  label.htmlFor = toggle.id;
  const swatch = document.createElement("i");
  swatch.className = "environment-field-swatch";
  swatch.style.background = "#d52f43";
  label.append(toggle, swatch, document.createTextNode("Traffic lights"));
  const row = document.createElement("div");
  row.className = "environment-field-control";
  const help = element("traffic-light-help");
  help.hidden = false;
  row.append(label, help);
  element("environment-layer-controls").append(row);
  return toggle;
}

function connectSignals(map: Map, config: SignalMapConfig): void {
  const status = element("traffic-light-status");
  const popup = element("traffic-light-point-popup");
  const master = element<HTMLInputElement>("layer-environment");
  const toggle = signalControl();
  let controller: AbortController | undefined;
  let generation = 0;
  let pinned = false;
  map.addSource(SOURCE, {
    type: "geojson",
    data: EMPTY,
    attribution:
      '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> (ODbL)',
  });
  map.addLayer({
    id: SOURCE,
    type: "circle",
    source: SOURCE,
    layout: { visibility: "none" },
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 4, 17, 7],
      "circle-color": ["match", ["get", "kind"], "pedestrian", "#d52f43", "#da960e"],
      "circle-stroke-color": "#fff",
      "circle-stroke-width": 1.5,
    },
  });
  const source = () => map.getSource(SOURCE) as GeoJSONSource;
  async function refresh(): Promise<void> {
    pinned = false;
    popup.hidePopover();
    controller?.abort();
    const current = ++generation;
    const visible = Boolean(master.checked && toggle.checked);
    map.setLayoutProperty(SOURCE, "visibility", visible ? "visible" : "none");
    if (!visible) {
      status.hidden = true;
      return;
    }
    status.hidden = false;
    if (map.getZoom() < config.minimum_zoom) {
      source().setData(EMPTY);
      status.textContent = "Zoom in to see mapped traffic lights.";
      return;
    }
    controller = new AbortController();
    const bounds = map.getBounds();
    const parameters = new URLSearchParams({
      dataset: config.dataset_id,
      west: String(bounds.getWest()),
      south: String(bounds.getSouth()),
      east: String(bounds.getEast()),
      north: String(bounds.getNorth()),
    });
    status.textContent = "Loading mapped traffic lights…";
    try {
      const response = await fetch(`${config.url}?${parameters}`, { signal: controller.signal });
      if (!response.ok)
        throw new Error(
          response.status === 409
            ? "Signal dataset changed. Reload the map."
            : "Traffic-light evidence could not load.",
        );
      const data = (await response.json()) as FeatureCollection & { status: string };
      if (current !== generation) return;
      source().setData(data);
      status.textContent =
        data.status === "zoom_in"
          ? "Zoom in to see mapped traffic lights."
          : `${data.features.length} signal markers`;
    } catch (error: unknown) {
      if (current !== generation) return;
      source().setData(EMPTY);
      status.textContent =
        error instanceof Error ? error.message : "Traffic-light evidence unavailable.";
    }
  }
  master.disabled = false;
  master.addEventListener("change", () => void refresh());
  toggle.addEventListener("change", () => void refresh());
  map.on("moveend", () => void refresh());
  map.on("mousemove", SOURCE, (event: MapLayerMouseEvent) => {
    if (pinned) return;
    const properties = event.features?.[0]?.properties;
    if (properties) showSignalPopup(popup, properties, event);
  });
  map.on("click", SOURCE, (event: MapLayerMouseEvent) => {
    const properties = event.features?.[0]?.properties;
    if (properties) {
      pinned = true;
      showSignalPopup(popup, properties, event);
    }
  });
  map.on("mouseleave", SOURCE, () => {
    if (!pinned) popup.hidePopover();
  });
  popup.addEventListener("toggle", () => {
    if (!popup.matches(":popover-open")) pinned = false;
  });
  status.hidden = true;
}

function showSignalPopup(
  popup: HTMLElement,
  properties: Record<string, unknown>,
  event: MapLayerMouseEvent,
): void {
  popup.textContent = signalDetails(properties);
  popup.showPopover();
  const bounds = popup.getBoundingClientRect();
  popup.style.left = `${Math.max(8, Math.min(event.originalEvent.clientX + 12, innerWidth - bounds.width - 8))}px`;
  popup.style.top = `${Math.max(8, Math.min(event.originalEvent.clientY + 12, innerHeight - bounds.height - 8))}px`;
}

export async function startTrafficLights(map: Map): Promise<void> {
  const status = element("traffic-light-status");
  status.hidden = false;
  try {
    const response = await fetch("/api/paths/signals/config", { cache: "no-store" });
    if (!response.ok)
      throw new Error(
        response.status === 404
          ? "Traffic-light layer unavailable."
          : "Traffic lights could not load. Reload to retry.",
      );
    connectSignals(map, (await response.json()) as SignalMapConfig);
  } catch (error: unknown) {
    status.textContent =
      error instanceof Error ? error.message : "Traffic-light map evidence unavailable.";
  }
}

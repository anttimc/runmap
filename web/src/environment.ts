/** Inspect overlapping, unmodified OSM source evidence independently of route planning. */
import {
  Map,
  NavigationControl,
  setWorkerUrl,
  type FilterSpecification,
  type ExpressionSpecification,
  type LngLat,
} from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import type { FeatureCollection } from "geojson";
import { openStreetMapStyle, FIRST_LABEL_LAYER_ID } from "./map-style";
import { element } from "./dom";
import "./environment.css";
import { startRasterLayers, type RasterControls } from "./environment-rasters";

interface InspectionConfig {
  manifest: {
    request: { id: string };
    features: { sha256: string; bytes: number };
    feature_count: number;
    osm_base_timestamps: string[];
    limitations: string[];
    query_bbox_wgs84_left_bottom_right_top: [number, number, number, number];
  };
  features_url: string;
  boundaries: FeatureCollection;
}

interface SourceLayer {
  id: string;
  label: string;
  color: string;
  visible: boolean;
  geometry: "Polygon" | "LineString";
  filter: ExpressionSpecification;
}

const majorRoads = ["motorway", "trunk", "primary", "secondary", "tertiary"];
const roadClasses = majorRoads.flatMap((road) => [road, `${road}_link`]);
const layers: SourceLayer[] = [
  {
    id: "forest",
    label: "Woodland polygons",
    color: "#238447",
    visible: true,
    geometry: "Polygon",
    filter: ["any", ["==", ["get", "natural"], "wood"], ["==", ["get", "landuse"], "forest"]],
  },
  {
    id: "water-areas",
    label: "Water polygons",
    color: "#168cd1",
    visible: true,
    geometry: "Polygon",
    filter: [
      "any",
      ["==", ["get", "natural"], "water"],
      ["has", "water"],
      ["in", ["get", "landuse"], ["literal", ["reservoir", "basin"]]],
      ["has", "waterway"],
    ],
  },
  {
    id: "water-lines",
    label: "Waterways / coastline",
    color: "#0867b0",
    visible: true,
    geometry: "LineString",
    filter: ["any", ["has", "waterway"], ["==", ["get", "natural"], "coastline"]],
  },
  {
    id: "wetlands",
    label: "Wetland polygons",
    color: "#397f83",
    visible: false,
    geometry: "Polygon",
    filter: ["==", ["get", "natural"], "wetland"],
  },
  {
    id: "parks",
    label: "Parks",
    color: "#9b9c34",
    visible: false,
    geometry: "Polygon",
    filter: ["==", ["get", "leisure"], "park"],
  },
  {
    id: "nature-reserves",
    label: "Nature reserves (designation)",
    color: "#267f73",
    visible: false,
    geometry: "Polygon",
    filter: ["==", ["get", "leisure"], "nature_reserve"],
  },
  {
    id: "buildings",
    label: "Buildings",
    color: "#71635a",
    visible: false,
    geometry: "Polygon",
    filter: ["has", "building"],
  },
  {
    id: "paths",
    label: "Mapped paths (access unverified)",
    color: "#373c3e",
    visible: false,
    geometry: "LineString",
    filter: [
      "in",
      ["get", "highway"],
      ["literal", ["path", "footway", "cycleway", "pedestrian", "steps", "track"]],
    ],
  },
  {
    id: "roads",
    label: "Major road classes (incl. links)",
    color: "#d54b30",
    visible: true,
    geometry: "LineString",
    filter: ["in", ["get", "highway"], ["literal", roadClasses]],
  },
];

function layerId(layer: SourceLayer): string {
  return `environment-${layer.id}`;
}

function addSourceLayers(map: Map): void {
  const before = map.getLayer(FIRST_LABEL_LAYER_ID) ? FIRST_LABEL_LAYER_ID : undefined;
  for (const layer of layers) {
    const filter: FilterSpecification = [
      "all",
      ["==", ["geometry-type"], layer.geometry],
      layer.filter,
    ];
    const layout = { visibility: layer.visible ? ("visible" as const) : ("none" as const) };
    if (layer.geometry === "Polygon") {
      map.addLayer(
        {
          id: layerId(layer),
          type: "fill",
          source: "environment",
          filter,
          layout,
          paint: {
            "fill-color": layer.color,
            "fill-opacity": 0.28,
            "fill-outline-color": layer.color,
          },
        },
        before,
      );
    } else {
      map.addLayer(
        {
          id: layerId(layer),
          type: "line",
          source: "environment",
          filter,
          layout,
          paint: {
            "line-color": layer.color,
            "line-width": layer.id === "roads" ? 3 : 2,
            "line-opacity": 0.85,
            ...(layer.id === "paths" ? { "line-dasharray": [2, 1] } : {}),
          },
        },
        before,
      );
    }
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = layer.visible;
    input.addEventListener("change", () => {
      map.setLayoutProperty(layerId(layer), "visibility", input.checked ? "visible" : "none");
      element("inspection-selection").textContent =
        "Layers changed. Click the map to inspect again.";
    });
    const swatch = document.createElement("i");
    swatch.style.background = layer.color;
    label.append(input, swatch, document.createTextNode(layer.label));
    element("inspection-layers").append(label);
  }
}

interface InspectedObject {
  properties: Record<string, unknown>;
  categories: Set<string>;
}

function objectsAt(map: Map, location: LngLat): globalThis.Map<string, InspectedObject> {
  const point = map.project(location);
  const results = map.queryRenderedFeatures(
    [
      [point.x - 4, point.y - 4],
      [point.x + 4, point.y + 4],
    ],
    { layers: layers.map(layerId) },
  );
  const objects = new globalThis.Map<string, InspectedObject>();
  for (const result of results) {
    const properties = result.properties;
    const key = `${properties.osm_type}/${properties.osm_id}`;
    const item = objects.get(key) ?? { properties, categories: new Set<string>() };
    item.categories.add(layers.find((layer) => layerId(layer) === result.layer.id)!.label);
    objects.set(key, item);
  }
  return objects;
}

function objectDetails(key: string, item: InspectedObject): HTMLDetailsElement {
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = `${String(item.properties.name ?? key)} · ${[...item.categories].join(", ")}`;
  const tags = document.createElement("pre");
  tags.textContent = JSON.stringify(item.properties, null, 2);
  const link = document.createElement("a");
  link.textContent = `Open ${key} on OpenStreetMap`;
  if (/^(node|way|relation)\/\d+$/.test(key)) {
    link.href = `https://www.openstreetmap.org/${key}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  details.append(summary, tags, link);
  return details;
}

function inspectLocation(map: Map, location: LngLat): void {
  const objects = objectsAt(map, location);
  const panel = element("inspection-selection");
  const heading = document.createElement("p");
  heading.textContent = `${location.lat.toFixed(6)}, ${location.lng.toFixed(6)} · ${objects.size} visible objects within 4 screen pixels`;
  panel.replaceChildren(heading);
  if (!objects.size) {
    panel.append(
      document.createTextNode(
        "No visible source evidence here. This does not establish absence in the real world.",
      ),
    );
  }
  for (const [key, item] of objects) {
    panel.append(objectDetails(key, item));
  }
}

async function start(): Promise<void> {
  const response = await fetch("/api/environment/config", { cache: "no-store" });
  if (response.status === 404) {
    element("inspection-status").textContent = "Environment pilot unavailable.";
    element("inspection-unavailable").hidden = false;
    element("inspection-tools").hidden = true;
    element("inspection-map").hidden = true;
    document.body.classList.add("is-unavailable");
    return;
  }
  if (!response.ok) throw new Error("Unable to load pilot metadata. Reload to retry.");
  const config = (await response.json()) as InspectionConfig;
  const manifest = config.manifest;
  const metadata = element("inspection-metadata");
  const identity = document.createElement("p");
  identity.textContent = `${manifest.request.id} · ${manifest.feature_count.toLocaleString()} source features · ${(manifest.features.bytes / 1e6).toFixed(1)} MB. OSM base: ${manifest.osm_base_timestamps.join(", ") || "unknown"}. SHA256: ${manifest.features.sha256}`;
  metadata.append(identity);
  for (const limitation of manifest.limitations) {
    const paragraph = document.createElement("p");
    paragraph.textContent = limitation;
    metadata.append(paragraph);
  }
  setWorkerUrl(maplibreWorkerUrl);
  const map = new Map({
    container: "inspection-map",
    style: openStreetMapStyle(),
    bounds: manifest.query_bbox_wgs84_left_bottom_right_top,
    fitBoundsOptions: { padding: 25 },
  });
  map.addControl(new NavigationControl());
  let rasterControls: RasterControls | undefined;
  map.on("error", (event) => {
    if ("sourceId" in event && event.sourceId === "environment") {
      element("inspection-status").textContent = "Source geometry failed to load. Reload to retry.";
    }
  });
  map.on("load", () => {
    element("inspection-status").textContent = "Loading source geometry…";
    map.addSource("environment", {
      type: "geojson",
      data: config.features_url,
      tolerance: 0,
      attribution: "© OpenStreetMap contributors (ODbL)",
    });
    map.addSource("environment-bounds", { type: "geojson", data: config.boundaries });
    addSourceLayers(map);
    for (const kind of ["core", "query"]) {
      map.addLayer({
        id: `boundary-${kind}`,
        type: "line",
        source: "environment-bounds",
        filter: ["==", ["get", "kind"], kind],
        paint: {
          "line-color": "#7048a5",
          "line-width": 2,
          ...(kind === "query" ? { "line-dasharray": [4, 3] } : {}),
        },
      });
    }
    void startRasterLayers(map, manifest.features.sha256).then((controls) => {
      rasterControls = controls;
    });
    map.on("click", (event) => {
      inspectLocation(map, event.lngLat);
      rasterControls?.inspect(event.lngLat);
    });
    element("inspect-center").addEventListener("click", () => {
      inspectLocation(map, map.getCenter());
      rasterControls?.inspect(map.getCenter());
    });
    element<HTMLButtonElement>("inspect-center").disabled = false;
    element<HTMLFieldSetElement>("inspection-layers").disabled = false;
    element<HTMLButtonElement>("fit-pilot").disabled = false;
  });
  map.on("sourcedata", (event) => {
    if (event.sourceId === "environment" && event.isSourceLoaded) {
      element("inspection-status").textContent = `Ready · ${manifest.request.id} · source geometry`;
    }
  });
  element("fit-pilot").addEventListener("click", () => {
    map.fitBounds(manifest.query_bbox_wgs84_left_bottom_right_top, { padding: 25 });
  });
}

void start().catch((error: unknown) => {
  element("inspection-status").textContent =
    error instanceof Error ? error.message : "Inspection map failed to start.";
});

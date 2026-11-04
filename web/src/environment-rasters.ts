/** Display prepared raster proxies separately from raw OSM evidence and cancel stale inspections. */
import type { LngLat, Map } from "maplibre-gl";
import { element } from "./dom.ts";

interface RasterConfig {
  manifest: {
    dataset_id: string;
    method: string;
    config: {
      resolution_m: number;
      schema_version?: number;
      park_greenery_weight?: number;
      reserve_greenery_weight?: number;
      include_all_water_polygons?: boolean;
      road_noise?: {
        reference_distance_m: number;
        car_intensity_w_m2: number;
        intensity_reference_w_m2: number;
        discomfort_floor_db: number;
        discomfort_full_db: number;
      } | null;
    };
    previews: {
      coordinates: [[number, number], [number, number], [number, number], [number, number]];
      layers?: string[];
    };
    limitations: string[];
  };
  image_template: string;
  sample_url: string;
}

interface RasterSample {
  dataset_id: string;
  status: "mapped_context_only" | "outside_pilot";
  values: Record<string, number | null> | null;
  evidence?: Record<
    string,
    {
      count: number;
      nearest_or_strongest: {
        id: string;
        category: string;
        distance_m: number;
        tags: Record<string, unknown>;
        modeled_noise_db?: number;
        spreading_exponent?: number;
      }[];
    }
  >;
}

export interface RasterControls {
  inspect(location: LngLat): void;
}

const fields = [
  { id: "greenery", label: "Greenery", color: "#1e8a40" },
  { id: "forest", label: "Forest surroundings", color: "#1e8a40" },
  { id: "water", label: "Water proximity", color: "#1478e0" },
  { id: "traffic", label: "Road exposure proxy", color: "#dc3720" },
  { id: "wetland", label: "Wetland presence", color: "#8b44af" },
  { id: "evidence_gap", label: "Review candidates (hatching)", color: "#d68d0c" },
];

function addRasterControls(map: Map, config: RasterConfig): void {
  const available =
    config.manifest.previews.layers ??
    fields.filter((field) => field.id !== "greenery").map((field) => field.id);
  for (const field of fields.filter((field) => available.includes(field.id))) {
    const id = `raster-${field.id}`;
    map.addSource(id, {
      type: "image",
      url: config.image_template.replace("{layer}", field.id),
      coordinates: config.manifest.previews.coordinates,
    });
    map.addLayer(
      {
        id,
        type: "raster",
        source: id,
        layout: { visibility: "none" },
        paint: { "raster-opacity": 0.8, "raster-fade-duration": 0, "raster-resampling": "nearest" },
      },
      map.getLayer("environment-forest") ? "environment-forest" : undefined,
    );
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.raster = field.id;
    input.addEventListener("change", () =>
      map.setLayoutProperty(id, "visibility", input.checked ? "visible" : "none"),
    );
    const swatch = document.createElement("i");
    swatch.style.background = `linear-gradient(to right, transparent, ${field.color})`;
    label.append(input, swatch, document.createTextNode(field.label));
    element("raster-layers").append(label);
  }
  element<HTMLFieldSetElement>("raster-layers").disabled = false;
}

function renderSample(sample: RasterSample): void {
  const panel = element("raster-selection");
  if (!sample.values) {
    panel.textContent = "Outside the prepared pilot. No raster scores are available here.";
    return;
  }
  const description = document.createElement("p");
  description.textContent =
    "Mapped-context baseline · 0–1 scores · completeness unverified. Zero can mean no mapped evidence or a modeled value below the display floor; it does not establish absence.";
  const list = document.createElement("dl");
  for (const field of fields.filter(
    (field) =>
      field.id !== "evidence_gap" &&
      field.id in sample.values! &&
      !(field.id === "forest" && "greenery" in sample.values!),
  )) {
    const key = document.createElement("dt");
    key.textContent = field.label;
    const value = document.createElement("dd");
    value.textContent = sample.values[field.id]?.toFixed(3) ?? "Unavailable";
    list.append(key, value);
  }
  if ("traffic_noise_db" in sample.values) {
    const key = document.createElement("dt");
    key.textContent = "Dominant-road level (model)";
    const value = document.createElement("dd");
    value.textContent =
      sample.values.traffic_noise_db === null
        ? "No modeled source"
        : `${sample.values.traffic_noise_db.toFixed(1)} dB`;
    list.append(key, value);
  }
  const gap = document.createElement("p");
  gap.textContent = `Context support: ${sample.values.context_support === 1 ? "retained halo available" : "incomplete"}.`;
  if (!("greenery" in sample.values))
    gap.textContent += ` Unclassified park/woodland candidates: ${Number(sample.values.forest_evidence_gap).toFixed(3)}. Other-water candidates: ${Number(sample.values.water_evidence_gap).toFixed(3)}.`;
  if ("traffic_context_support" in sample.values)
    gap.textContent +=
      sample.values.traffic_context_support === 1
        ? " Road context: exterior dominance excluded under model assumptions."
        : " Road context: a source outside retained coverage could dominate; score may be underestimated.";
  panel.replaceChildren(description, list, gap);
  const evidence = document.createElement("details");
  const title = document.createElement("summary");
  title.textContent = "Nearby contributing evidence and candidates";
  const content = document.createElement("div");
  for (const [group, result] of Object.entries(sample.evidence ?? {})) {
    if (!result.count) continue;
    const heading = document.createElement("p");
    heading.textContent = `${group.replaceAll("_", " ")}: ${result.count} objects (up to 5 shown)`;
    content.append(heading);
    for (const item of result.nearest_or_strongest) {
      const row = document.createElement("p");
      const name = String(item.tags.name ?? item.id);
      const tags = ["highway", "maxspeed", "lanes", "water", "leisure", "wetland"]
        .filter((key) => item.tags[key] !== undefined)
        .map((key) => `${key}=${String(item.tags[key])}`)
        .join(", ");
      row.textContent = `${name} (${item.id}) · ${item.distance_m.toFixed(1)} m · ${tags}`;
      if (item.modeled_noise_db !== undefined)
        row.textContent += ` · ${item.modeled_noise_db.toFixed(1)} dB model, ${item.spreading_exponent === 1 ? "ideal line" : item.spreading_exponent === 2 ? "point" : "attenuated line"} assumption (p=${item.spreading_exponent})`;
      content.append(row);
    }
  }
  evidence.append(title, content);
  const raw = document.createElement("details");
  const rawTitle = document.createElement("summary");
  rawTitle.textContent = "All raster channels and identity";
  const data = document.createElement("pre");
  data.textContent = JSON.stringify({ dataset: sample.dataset_id, values: sample.values }, null, 2);
  raw.append(rawTitle, data);
  panel.append(evidence, raw);
}

export async function startRasterLayers(
  map: Map,
  source: string,
): Promise<RasterControls | undefined> {
  const status = element("raster-status");
  try {
    const response = await fetch(`/api/environment/rasters/config?source=${source}`, {
      cache: "no-store",
    });
    if (!response.ok) {
      status.textContent =
        response.status === 404
          ? "Rasters not prepared. Source inspection remains available."
          : "Raster/source mismatch or request failure. Reload or prepare matching fields.";
      return;
    }
    const config = (await response.json()) as RasterConfig;
    map.on("error", (event) => {
      if ("sourceId" in event && String(event.sourceId).startsWith("raster-"))
        status.textContent =
          "A raster preview failed to load. Reload to retry; source inspection remains available.";
    });
    addRasterControls(map, config);
    status.textContent = `${config.manifest.config.resolution_m} m mapped-context baseline. Layers start off. Color intensity: 0 transparent → 1 strong. Hatching marks review candidates and limited road context, not all missing data.`;
    const notes = document.createElement("details");
    const title = document.createElement("summary");
    title.textContent = "Raster method and limitations";
    notes.append(title);
    if ((config.manifest.config.schema_version ?? 1) >= 3) {
      const greenery = document.createElement("p");
      const waterAreas = config.manifest.config.include_all_water_polygons
        ? "all mapped water-polygon shores"
        : "tagged lake shores";
      greenery.textContent = `Greenery takes the strongest of woodland surroundings (weight 1), park coverage (${config.manifest.config.park_greenery_weight}), and reserve coverage (${config.manifest.config.reserve_greenery_weight}). Park/reserve contributions stay inside polygon footprints. Water includes ${waterAreas} and coastline; line streams/ditches remain excluded. Hatching marks limited road context.`;
      notes.append(greenery);
    }
    const noise = config.manifest.config.road_noise;
    if (noise) {
      const reference = document.createElement("p");
      const level = 10 * Math.log10(noise.car_intensity_w_m2 / noise.intensity_reference_w_m2);
      reference.textContent = `Assumed car reference: ${level.toFixed(1)} dB at ${noise.reference_distance_m} m. Discomfort scale: ${noise.discomfort_floor_db}–${noise.discomfort_full_db} dB. These are configurable model assumptions, not measurements or dBA.`;
      notes.append(reference);
    }
    for (const text of [config.manifest.method, ...config.manifest.limitations]) {
      const paragraph = document.createElement("p");
      paragraph.textContent = text;
      notes.append(paragraph);
    }
    status.after(notes);
    let pending: AbortController | undefined;
    return {
      inspect(location): void {
        pending?.abort();
        const controller = new AbortController();
        pending = controller;
        element("raster-selection").textContent = "Loading raster cell…";
        void fetch(`${config.sample_url}&longitude=${location.lng}&latitude=${location.lat}`, {
          signal: controller.signal,
        })
          .then(async (response) => {
            if (!response.ok)
              throw new Error("Raster inspection failed; reload if the dataset changed.");
            return (await response.json()) as RasterSample;
          })
          .then((sample) => {
            if (!controller.signal.aborted) renderSample(sample);
          })
          .catch((error: unknown) => {
            if (!controller.signal.aborted)
              element("raster-selection").textContent =
                error instanceof Error ? error.message : "Raster inspection failed.";
          });
      },
    };
  } catch {
    status.textContent = "Raster layers could not load. Source inspection remains available.";
  }
}

/** Load route elevation on demand and link chart exploration to map locations. */
import { Marker, type Map } from "maplibre-gl";
import { loadRouteProfile } from "./api";
import type { Candidate, ProfileInterval, RouteProfile } from "./api-types";
import { element } from "./dom";

const ns = "http://www.w3.org/2000/svg";

function svgElement<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attributes: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const node = document.createElementNS(ns, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}

function paragraph(value: string): HTMLParagraphElement {
  const node = document.createElement("p");
  node.textContent = value;
  return node;
}

function elevationChart(profile: RouteProfile): {
  svg: SVGSVGElement;
  x: (distance: number) => number;
} {
  const svg = svgElement("svg", {
    viewBox: "0 0 600 180",
    role: "img",
    "aria-label": "Elevation in metres along route distance in kilometres",
  });
  const heights = profile.intervals
    .flatMap((item) => [item.start_elevation_m, item.end_elevation_m])
    .filter((value): value is number => value !== null);
  const minimum = heights.length ? heights.reduce((a, b) => Math.min(a, b)) : 0;
  const maximum = heights.length ? heights.reduce((a, b) => Math.max(a, b)) : 0;
  const low = Math.floor(minimum / 5) * 5;
  const high = Math.max(low + 5, Math.ceil(maximum / 5) * 5);
  const x = (distance: number): number => 52 + (distance / Math.max(profile.distance_m, 1)) * 530;
  const y = (height: number): number => 140 - ((height - low) / (high - low)) * 110;
  for (const elevation of [low, (low + high) / 2, high]) {
    svg.append(
      svgElement("line", {
        x1: 52,
        x2: 582,
        y1: y(elevation),
        y2: y(elevation),
        stroke: "#d8dfe2",
      }),
    );
    const label = svgElement("text", { x: 46, y: y(elevation) + 4, "text-anchor": "end" });
    label.textContent = `${elevation.toFixed(1)} m`;
    svg.append(label);
  }
  for (const distance of [0, profile.distance_m / 2, profile.distance_m]) {
    const label = svgElement("text", {
      x: x(distance),
      y: 165,
      "text-anchor": distance === 0 ? "start" : distance === profile.distance_m ? "end" : "middle",
    });
    label.textContent = `${(distance / 1000).toFixed(2)} km`;
    svg.append(label);
  }
  const paths = ["", ""];
  for (const item of profile.intervals) {
    if (item.start_elevation_m === null || item.end_elevation_m === null) continue;
    const uncertain = item.quality_flags.length > 0 ? 1 : 0;
    paths[uncertain] +=
      `M${x(item.start_m)},${y(item.start_elevation_m)}L${x(item.end_m)},${y(item.end_elevation_m)}`;
  }
  paths.forEach((path, index) =>
    svg.append(
      svgElement("path", {
        d: path,
        fill: "none",
        stroke: index ? "#8b619e" : "#2367c0",
        "stroke-width": 2,
        "stroke-dasharray": index ? "4 3" : "none",
      }),
    ),
  );
  if (profile.turnaround_m !== null) {
    const position = x(profile.turnaround_m);
    svg.append(
      svgElement("line", {
        x1: position,
        x2: position,
        y1: 25,
        y2: 140,
        stroke: "#805523",
        "stroke-dasharray": "3 3",
      }),
    );
    const label = svgElement("text", { x: position, y: 17, "text-anchor": "middle" });
    label.textContent = "Turnaround";
    svg.append(label);
  }
  return { svg, x };
}

function profileChart(
  profile: RouteProfile,
  hover: (interval?: ProfileInterval) => void,
): HTMLElement {
  const container = document.createElement("div");
  const { svg, x } = elevationChart(profile);
  const cursor = svgElement("line", {
    x1: 52,
    x2: 52,
    y1: 25,
    y2: 140,
    stroke: "#193c38",
    visibility: "hidden",
  });
  svg.append(cursor);
  const readout = paragraph("");
  readout.className = "sample-readout";
  readout.setAttribute("aria-live", "polite");
  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = "0";
  slider.max = String(Math.max(0, profile.intervals.length - 1));
  slider.step = "1";
  slider.value = "0";
  slider.setAttribute("aria-label", "Explore route elevation by distance");
  slider.disabled = profile.intervals.length === 0;
  function select(index: number): void {
    const item = profile.intervals[index];
    if (!item) return;
    slider.value = String(index);
    cursor.setAttribute("x1", String(x(item.start_m)));
    cursor.setAttribute("x2", String(x(item.start_m)));
    cursor.setAttribute("visibility", "visible");
    const value = `${(item.start_m / 1000).toFixed(2)} km · ${item.start_elevation_m === null ? "Elevation unavailable" : `${item.start_elevation_m.toFixed(1)} m elevation`} · ${item.grade_percent === null ? "Grade unavailable" : `${item.grade_percent.toFixed(1)}% grade`}${item.quality_flags.length ? ` · ${item.quality_flags.join(", ").replaceAll("_", " ")}` : ""}`;
    readout.textContent = value;
    slider.setAttribute("aria-valuetext", value);
    hover(item);
  }
  slider.addEventListener("input", () => select(Number(slider.value)));
  slider.addEventListener("focus", () => select(Number(slider.value)));
  svg.addEventListener("pointermove", (event) => {
    const bounds = svg.getBoundingClientRect();
    const distance = Math.max(
      0,
      Math.min(
        profile.distance_m,
        ((((event.clientX - bounds.left) / bounds.width) * 600 - 52) / 530) * profile.distance_m,
      ),
    );
    const index = profile.intervals.findIndex((item) => item.end_m >= distance);
    select(index < 0 ? profile.intervals.length - 1 : index);
  });
  svg.addEventListener("pointerleave", () => {
    if (document.activeElement !== slider) {
      cursor.setAttribute("visibility", "hidden");
      hover();
    }
  });
  slider.addEventListener("blur", () => {
    cursor.setAttribute("visibility", "hidden");
    hover();
  });
  container.append(svg, slider, readout);
  return container;
}

export function startRouteProfile(
  map: Map,
  datasetId: string,
): { open: (candidate: Candidate) => Promise<void>; close: () => void } {
  const panel = element("route-inspection");
  const content = element("route-profile-content");
  const marker = new Marker({ color: "#193c38", scale: 0.65 });
  marker.getElement().title = "Elevation profile location";
  const cache = new globalThis.Map<string, RouteProfile>();
  let abort: AbortController | undefined;
  let sequence = 0;
  function close(): void {
    abort?.abort();
    sequence++;
    element("route-profile-help").hidePopover();
    panel.querySelector(".help-trigger")!.setAttribute("aria-expanded", "false");
    panel.hidden = true;
    marker.remove();
  }
  element("route-profile-close").addEventListener("click", () => {
    close();
    element("run-elevation").focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      close();
      element("run-elevation").focus();
    }
  });
  async function open(candidate: Candidate): Promise<void> {
    close();
    panel.hidden = false;
    content.replaceChildren(paragraph("Loading route elevation…"));
    abort = new AbortController();
    const current = sequence;
    const legs = candidate.legs.map((leg) => leg.traversals);
    const key = JSON.stringify([datasetId, legs]);
    try {
      const profile = cache.get(key) ?? (await loadRouteProfile(datasetId, legs, abort.signal));
      if (current !== sequence) return;
      if (profile.dataset_id !== datasetId) throw new Error("Dataset changed — reload the map.");
      if (cache.size >= 8) cache.delete(cache.keys().next().value!);
      cache.set(key, profile);
      element("route-profile-help").textContent =
        `Hover over the chart or use the distance slider to explore the route. Estimated ground elevation with ${profile.wavelength_m} m smoothing. Gaps have no elevation data; dashed purple sections have terrain uncertainty. Bridges and tunnels show ground height, which may differ from the route.`;
      const summary = `${(profile.distance_m / 1000).toFixed(2)} km · ↑${Math.round(profile.ascent_m)} m / ↓${Math.round(profile.descent_m)} m · ${Math.round(profile.valid_length_fraction * 100)}% elevation coverage${profile.totals_complete ? "" : " · ascent/descent incomplete"}`;
      content.replaceChildren(
        paragraph(summary),
        profileChart(profile, (item) => {
          if (item) marker.setLngLat(item.coordinates[0]).addTo(map);
          else marker.remove();
        }),
      );
      if (profile.valid_length_fraction === 0)
        content.append(paragraph("Elevation data is unavailable for this route."));
    } catch (error) {
      if (current !== sequence || (error as Error).name === "AbortError") return;
      content.replaceChildren(paragraph(`Route elevation failed. ${(error as Error).message}`));
    }
  }
  return { open, close };
}

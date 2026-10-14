import { LngLatBounds, Marker, type GeoJSONSource, type Map } from "maplibre-gl";
import type { FeatureCollection, Feature, LineString } from "geojson";
import { mapPadding } from "./layout";

type Location = [number, number];
interface Snap { longitude: number; latitude: number; offset_m: number }
interface Leg { coordinates: Location[]; distance_m: number }
interface Candidate {
  outbound_left: boolean;
  outbound: Leg; returning: Leg; distance_m: number; repeated_distance_m: number;
  closest_centre_m: number; encloses_centre: boolean | null; equivalent_to: number | null;
}
interface RunResponse {
  dataset_id: string; status: string; home: Snap | null; turnaround: Snap | null;
  candidates: Candidate[]; elapsed_s: number; coverage_note: string;
}
export interface RunControls {
  active: () => boolean;
  picking: () => boolean;
  click: (location: Location) => boolean;
}
interface Options {
  datasetId: string;
  getHome: () => Location | undefined;
  setHome: (location: Location | undefined) => void;
  policy: () => { surface: string; avoid_stairs: boolean; radius_m: number };
  modeChanged: (active: boolean) => void;
}
const empty: FeatureCollection = { type: "FeatureCollection", features: [] };
const el = <T extends HTMLElement = HTMLElement>(id: string): T => document.getElementById(id) as T;
const line = (coordinates: Location[], kind: string): Feature<LineString> =>
  ({ type: "Feature", properties: { kind }, geometry: { type: "LineString", coordinates } });

function separation(a: Location, b: Location): number {
  const radians = Math.PI / 180;
  return 6371000 * 2 * Math.asin(Math.min(1, Math.sqrt(Math.sin((b[1] - a[1]) * radians / 2) ** 2
    + Math.cos(a[1] * radians) * Math.cos(b[1] * radians) * Math.sin((b[0] - a[0]) * radians / 2) ** 2)));
}

function candidateName(candidate: Candidate): string {
  const outbound = candidate.outbound.coordinates;
  const returning = candidate.returning.coordinates;
  const retraced = outbound.length === returning.length && outbound.every((point, index) => {
    const reverse = returning[returning.length - 1 - index];
    return Math.abs(point[0] - reverse[0]) < 1e-9 && Math.abs(point[1] - reverse[1]) < 1e-9;
  });
  return retraced ? "Out and back" : candidate.outbound_left ? "Clockwise" : "Counter-clockwise";
}

function addRunLayers(map: Map): void {
  map.addSource("run-geometry", { type: "geojson", data: empty });
  map.addLayer({ id: "run-guides", type: "line", source: "run-geometry", filter: ["==", ["get", "kind"], "guide"],
    paint: { "line-color": "#55636a", "line-width": 1.5, "line-dasharray": [3, 3] } });
  map.addLayer({ id: "run-casing", type: "line", source: "run-geometry", filter: ["==", ["get", "kind"], "route"],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#fff", "line-width": 8, "line-opacity": .9 } });
  map.addLayer({ id: "run-route", type: "line", source: "run-geometry", filter: ["==", ["get", "kind"], "route"],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#2367c0", "line-width": 5 } });
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 32;
  const context = canvas.getContext("2d")!;
  context.beginPath(); context.moveTo(11, 7); context.lineTo(21, 16); context.lineTo(11, 25);
  context.lineJoin = context.lineCap = "round";
  context.strokeStyle = "#fff"; context.lineWidth = 7; context.stroke();
  context.strokeStyle = "#2367c0"; context.lineWidth = 3.5; context.stroke();
  map.addImage("run-direction", context.getImageData(0, 0, 32, 32));
  map.addLayer({ id: "run-directions", type: "symbol", source: "run-geometry", filter: ["==", ["get", "kind"], "route"],
    layout: { "symbol-placement": "line", "symbol-spacing": 95, "icon-image": "run-direction",
      "icon-size": .65, "icon-rotation-alignment": "map", "icon-keep-upright": false,
      "icon-padding": 8, "symbol-z-order": "source" } });
}

export function startRunPlanner(map: Map, options: Options): RunControls {
  let active = false;
  let choosing: "home" | "turnaround" | undefined;
  let turnaround: Location | undefined;
  let response: RunResponse | undefined;
  let selected = 0;
  let generating = false;
  let abort: AbortController | undefined;
  let sequence = 0;
  let snappingAbort: AbortController | undefined;
  let snapSequence = 0;
  let homeSnap: Snap | null = null;
  let turnaroundSnap: Snap | null = null;
  let pickStatus = "";
  const status = el("run-status");
  const avoidance = el<HTMLInputElement>("run-avoidance");
  const centre = el<HTMLInputElement>("run-centre");
  const inputsKey = (): string => JSON.stringify([options.getHome(), turnaround, options.policy(), avoidance.value, centre.checked]);
  let observedInputs = inputsKey();
  const markers = [new Marker({ color: "#193c38" }), new Marker({ color: "#805523" })];
  markers[0].getElement().title = "Home"; markers[1].getElement().title = "Turnaround";
  addRunLayers(map);

  function draw(): void {
    const home = options.getHome();
    const a: Location | undefined = homeSnap ? [homeSnap.longitude, homeSnap.latitude] : home;
    const b: Location | undefined = turnaroundSnap ? [turnaroundSnap.longitude, turnaroundSnap.latitude] : turnaround;
    const features: Feature<LineString>[] = [];
    markers.forEach(marker => marker.remove());
    if (active) {
      if (a) markers[0].setLngLat(a).addTo(map);
      if (b) markers[1].setLngLat(b).addTo(map);
      if (home && a) features.push(line([home, a], "guide"));
      if (turnaround && b) features.push(line([turnaround, b], "guide"));
      if (a && b) {
        const distance = separation(a, b);
        const midpoint: Location = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
        const radius = distance * Number(avoidance.value);
        features.push(line([a, b], "guide"));
        if (centre.checked) features.push(line(Array.from({ length: 65 }, (_, i): Location => {
          const angle = i * 2 * Math.PI / 64;
          return [midpoint[0] + radius * Math.cos(angle) / (111195 * Math.cos(midpoint[1] * Math.PI / 180)),
            midpoint[1] + radius * Math.sin(angle) / 111195];
        }), "guide"));
        el("run-avoidance-value").textContent = `${Math.round(radius)} m (${Math.round(Number(avoidance.value) * 100)}%)`;
      } else el("run-avoidance-value").textContent = `${Math.round(Number(avoidance.value) * 100)}% of endpoint separation`;
      const candidate = response?.candidates[selected];
      if (candidate) {
        features.push(line([...candidate.outbound.coordinates, ...candidate.returning.coordinates.slice(1)], "route"));
        el("run-direction-label").textContent = `${candidateName(candidate)} · arrows show travel direction`;
      }
    }
    (map.getSource("run-geometry") as GeoJSONSource).setData({ type: "FeatureCollection", features });
    for (const [id, location, snap] of [["run-home-value", home, homeSnap], ["run-turnaround-value", turnaround, turnaroundSnap]] as const) {
      el(id).textContent = location ? snap ? `Selected · ${snap.offset_m.toFixed(1)} m snap` : "Selected" : "Choose on map";
    }
    const generateButton = el<HTMLButtonElement>("run-generate");
    generateButton.disabled = generating || !home || !turnaround;
    generateButton.textContent = generating ? "Generating run…" : "Generate run";
    generateButton.setAttribute("aria-busy", String(generating));
    el<HTMLButtonElement>("run-fit").disabled = !response?.candidates.length;
    el("run-results").hidden = !response?.candidates.length;
  }
  function pick(value?: "home" | "turnaround"): void {
    const wasPicking = Boolean(choosing);
    if (value && !wasPicking) pickStatus = status.textContent ?? "";
    choosing = value;
    el("run-home").textContent = value === "home" ? "Cancel" : options.getHome() ? "Change" : "Set home";
    el("run-turnaround").textContent = value === "turnaround" ? "Cancel" : turnaround ? "Change" : "Set turnaround";
    el("run-home").setAttribute("aria-pressed", String(value === "home"));
    el("run-turnaround").setAttribute("aria-pressed", String(value === "turnaround"));
    el("run-home").setAttribute("aria-label", value === "home" ? "Cancel home selection" : options.getHome() ? "Change home" : "Set home on map");
    el("run-turnaround").setAttribute("aria-label", value === "turnaround" ? "Cancel turnaround selection" : turnaround ? "Change turnaround" : "Set turnaround on map");
    map.getCanvas().style.cursor = value ? "crosshair" : "";
    if (value) status.textContent = `Click the map to set ${value}.`;
    else if (wasPicking) status.textContent = pickStatus;
  }
  function invalidate(): void {
    observedInputs = inputsKey();
    abort?.abort(); ++sequence;
    generating = false;
    el("run-cancel").hidden = true;
    status.textContent = response?.candidates.length ? "Displayed run uses previous settings. Generate again to update." : "Choose endpoints, then generate a run.";
    draw();
  }
  async function snapEndpoints(): Promise<void> {
    snappingAbort?.abort(); snappingAbort = new AbortController();
    const signal = snappingAbort.signal;
    const current = ++snapSequence;
    homeSnap = turnaroundSnap = null;
    const home = options.getHome();
    const policy = options.policy();
    const locations = [home, turnaround];
    try {
      const snaps = await Promise.all(locations.map(async location => {
        if (!location) return null;
        const params = new URLSearchParams({ dataset_id: options.datasetId, longitude: String(location[0]),
          latitude: String(location[1]), surface: policy.surface, avoid_stairs: String(policy.avoid_stairs), radius_m: String(policy.radius_m) });
        const result = await fetch(`/api/paths/snap?${params}`, { signal });
        if (!result.ok) throw new Error("Endpoint snapping failed.");
        return (await result.json() as { snap: Snap | null }).snap;
      }));
      if (current !== snapSequence) return;
      [homeSnap, turnaroundSnap] = snaps;
      if (home && !homeSnap) status.textContent = "Home has no eligible path within the snap radius.";
      else if (turnaround && !turnaroundSnap) status.textContent = "Turnaround has no eligible path within the snap radius.";
      draw();
    } catch (error) {
      if (current === snapSequence && (error as Error).name !== "AbortError") status.textContent = (error as Error).message;
    }
  }
  function candidates(): void {
    el("run-candidates").replaceChildren();
    response?.candidates.forEach((candidate, index) => {
      const button = document.createElement("button");
      button.type = "button"; button.setAttribute("aria-pressed", String(index === selected));
      const shape = candidate.encloses_centre === true ? "Encloses centre" : candidate.encloses_centre === false
        ? "Does not enclose centre" : "Retracing or crossings: enclosure unclear";
      const equivalent = candidate.equivalent_to === null ? undefined : response?.candidates[candidate.equivalent_to];
      button.textContent = `${candidateName(candidate)} · ${(candidate.distance_m / 1000).toFixed(2)} km\n`
        + `${Math.round(candidate.repeated_distance_m)} m repeated${equivalent ? " · same route geometry" : ""}`;
      if (index === selected) el("run-detail-content").textContent =
        `Out ${(candidate.outbound.distance_m / 1000).toFixed(2)} km · back ${(candidate.returning.distance_m / 1000).toFixed(2)} km. ${shape} · closest approach ${Math.round(candidate.closest_centre_m)} m. ${candidateName(candidate) !== "Out and back" ? "Clockwise and counter-clockwise describe the planning preference; crossings and retracing may complicate the actual loop. " : ""}${response?.coverage_note ?? ""}`;
      button.addEventListener("click", () => { selected = index; candidates(); draw(); });
      el("run-candidates").append(button);
    });
  }
  async function generate(): Promise<void> {
    const home = options.getHome();
    if (!home || !turnaround) return;
    abort?.abort(); abort = new AbortController();
    const current = ++sequence;
    generating = true; draw();
    status.textContent = response?.candidates.length ? "Generating… displayed run uses previous settings." : "Generating run…";
    el("run-cancel").hidden = false;
    try {
      const result = await fetch("/api/paths/run", { method: "POST", headers: { "Content-Type": "application/json" },
        signal: abort.signal, body: JSON.stringify({ dataset_id: options.datasetId,
          home: { longitude: home[0], latitude: home[1] }, turnaround: { longitude: turnaround[0], latitude: turnaround[1] },
          ...options.policy(), settings: { avoidance_ratio: Number(avoidance.value), centre_enabled: centre.checked } }) });
      if (!result.ok) { const error = await result.json() as { detail?: string }; throw new Error(error.detail || "Run request failed."); }
      const next = await result.json() as RunResponse;
      if (current !== sequence) return;
      if (next.dataset_id !== options.datasetId) throw new Error("Dataset changed; reload the map.");
      response = next; selected = 0; homeSnap = next.home; turnaroundSnap = next.turnaround;
      const message = next.status === "complete" ? `${next.candidates.length} runs · ${next.elapsed_s.toFixed(2)} s.`
        : next.status === "deadline" ? "Search deadline reached. Any completed runs are shown."
        : next.status === "no_eligible_snap" ? `${!next.home ? "Home" : "Turnaround"} could not snap to an eligible path.`
        : "No complete return run connects these points under the current path filters.";
      status.textContent = message;
      candidates(); draw();
    } catch (error) {
      if (current === sequence && (error as Error).name !== "AbortError") status.textContent = `Run request failed: ${(error as Error).message}`;
    } finally {
      if (current === sequence) {
        generating = false; el("run-cancel").hidden = true; draw();
      }
    }
  }
  for (const [id, value] of [["tool-explore", false], ["tool-run", true]] as const) {
    el<HTMLButtonElement>(id).disabled = false;
    el(id).addEventListener("click", () => {
      if (active === value) return;
      active = value; pick();
      el("run-controls").hidden = !active; el("explore-controls").hidden = active;
      el("run-footer").hidden = !active;
      for (const [tabId, selected] of [["tool-explore", !active], ["tool-run", active]] as const) {
        el(tabId).setAttribute("aria-selected", String(selected)); el(tabId).tabIndex = selected ? 0 : -1;
      }
      options.modeChanged(active); draw();
      if (active) {
        if (observedInputs !== inputsKey()) invalidate();
        void snapEndpoints();
      }
    });
  }
  el("run-home").addEventListener("click", () => pick(choosing === "home" ? undefined : "home"));
  el("run-turnaround").addEventListener("click", () => pick(choosing === "turnaround" ? undefined : "turnaround"));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && choosing) pick();
  });
  el("run-clear").addEventListener("click", () => {
    options.setHome(undefined); turnaround = undefined; response = undefined; homeSnap = turnaroundSnap = null;
    snappingAbort?.abort(); ++snapSequence; pick(); invalidate(); candidates();
  });
  avoidance.addEventListener("input", invalidate); centre.addEventListener("change", invalidate);
  for (const id of ["path-surface", "path-avoid-stairs", "outing-radius"]) el(id).addEventListener("change", () => {
    invalidate(); if (active) void snapEndpoints();
  });
  el("run-generate").addEventListener("click", () => { void generate(); });
  el("run-cancel").addEventListener("click", () => { invalidate(); status.textContent = "Cancelled. The server stops work at its deadline."; });
  el("run-fit").addEventListener("click", () => {
    const candidate = response?.candidates[selected]; if (!candidate) return;
    const bounds = new LngLatBounds();
    [...candidate.outbound.coordinates, ...candidate.returning.coordinates].forEach(point => bounds.extend(point));
    map.fitBounds(bounds, { padding: mapPadding(), maxZoom: 16 });
  });
  return { active: () => active, picking: () => Boolean(choosing), click: location => {
    if (!active || !choosing) return false;
    if (choosing === "home") options.setHome(location); else turnaround = location;
    pick(); invalidate(); void snapEndpoints(); return true;
  } };
}

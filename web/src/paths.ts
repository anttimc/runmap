import { Marker, type GeoJSONSource, type Map, type ExpressionSpecification } from "maplibre-gl";
import type { Feature, FeatureCollection, LineString, Point, Polygon } from "geojson";

interface PathConfig {
  status: string;
  dataset_id: string;
  coverage: { geometry: Polygon; kind: string };
  bounds_wgs84: [number, number, number, number];
  method: { wavelength_m: number; sampling_interval_m: number };
  min_zoom: number;
  snap_radius_m: number;
  default_budget_km: number;
  importer_notes: string[];
  initial_view: { longitude: number; latitude: number; zoom: number };
}
interface Inclusion {
  inclusion_state?: "included" | "beyond_budget" | "no_connection" | "excluded";
  inclusion_reason?: string;
  minimum_inclusion_m?: number | null;
  coverage_limited?: boolean;
}
interface Snap {
  segment_id: string;
  graph_offset_m: number;
  longitude: number;
  latitude: number;
  offset_m: number;
  description?: string;
}
interface StartResponse {
  status: string;
  requested: [number, number];
  snap: Snap | null;
}
interface Interval extends Inclusion {
  interval_index: number;
  start_m: number;
  end_m: number;
  grade_percent: number | null;
  valid: boolean;
  quality_flags: string[];
  geometry: number[][];
  relative_start_m: number | null;
  relative_end_m: number | null;
  profile_run: number | null;
}
interface Inspection {
  dataset_id: string;
  segment_id: string;
  reverse: boolean;
  segment: { geometry: number[][]; length_m: number; tags: Record<string, string>; forward: boolean; backward: boolean };
  intervals: Interval[];
  ascent_m: number;
  descent_m: number;
  valid_length_fraction: number;
  totals_complete: boolean;
  surface_class: string;
  eligible: boolean;
  traversal_permissions: { forward: boolean; backward: boolean };
  eligibility_reason: string | null;
  outing_label?: string;
  source: Record<string, unknown>;
  method: Record<string, unknown>;
  uphill_sections: { start_m: number; length_m: number; gain_m: number; mean_grade_percent: number; peak_grade_percent: number; uncertain: boolean }[];
}
interface Viewport extends FeatureCollection<LineString> {
  status: string;
  dataset_id: string;
  coverage_limited: boolean;
  start?: StartResponse;
  label?: string;
}

const empty: FeatureCollection = { type: "FeatureCollection", features: [] };
const colors = ["#087f8c", "#c09223", "#d45f2e", "#ae2849"];
const contextLayers = ["runner-path-casing", "runner-paths-maintained", "runner-paths-unmaintained", "runner-pedestrian-roads"];

function element<T extends HTMLElement>(id: string): T {
  return document.getElementById(id) as T;
}
function text(tag: string, content: string, className = ""): HTMLElement {
  const node = document.createElement(tag);
  node.textContent = content;
  node.className = className;
  return node;
}
function lineFeature(coordinates: number[][], properties = {}): Feature<LineString> {
  return { type: "Feature", properties, geometry: { type: "LineString", coordinates } };
}
function setData(map: Map, source: string, data: FeatureCollection): void {
  (map.getSource(source) as GeoJSONSource).setData(data);
}
function collection(features: Feature[]): FeatureCollection {
  return { type: "FeatureCollection", features };
}
async function request<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(response.status === 409 ? "Dataset changed — reload the map." : `Request failed (${response.status}).`);
  return await response.json() as T;
}
function reliable(interval: Interval): boolean {
  return interval.valid && !interval.quality_flags.some(flag => flag === "bridge" || flag === "tunnel");
}

function addPathLayers(map: Map, config: PathConfig): void {
  const before = map.getStyle().layers?.find(layer => layer.type === "symbol")?.id;
  map.addSource("path-coverage", { type: "geojson", data: { type: "Feature", properties: {}, geometry: config.coverage.geometry } });
  map.addLayer({ id: "path-coverage", type: "line", source: "path-coverage", paint: { "line-color": "#536773", "line-width": 1.5, "line-dasharray": [4, 3] } }, before);
  for (const id of ["path-intervals", "path-selection", "path-hover", "path-arrows"]) {
    map.addSource(id, { type: "geojson", data: empty });
  }
  map.addLayer({ id: "path-hit", type: "line", source: "path-intervals", minzoom: config.min_zoom,
    paint: { "line-width": 14, "line-opacity": 0 } }, before);
  map.addLayer({ id: "path-selection", type: "line", source: "path-selection",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#142b40", "line-width": 9, "line-opacity": .8 } }, before);
  map.addLayer({ id: "path-casing", type: "line", source: "path-intervals", minzoom: config.min_zoom,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#fffdf5", "line-width": 6 } }, before);
  const gradeColor: ExpressionSpecification = ["step", ["abs", ["coalesce", ["get", "grade_percent"], 0]], colors[0], 2, colors[1], 5, colors[2], 8, colors[3]];
  map.addLayer({ id: "path-grades", type: "line", source: "path-intervals", minzoom: config.min_zoom,
    filter: ["==", ["get", "quality"], "terrain_estimate"],
    layout: { "line-cap": "butt", "line-join": "round" },
    paint: { "line-width": 3.5, "line-color": gradeColor } }, before);
  map.addLayer({ id: "path-uncertain", type: "line", source: "path-intervals", minzoom: config.min_zoom,
    filter: ["!=", ["get", "quality"], "terrain_estimate"],
    paint: { "line-width": 3.5, "line-dasharray": [1.5, 1],
      "line-color": ["match", ["get", "quality"], "missing", "#727980", "#8b619e"] } }, before);
  map.addLayer({ id: "path-stairs", type: "line", source: "path-intervals", minzoom: config.min_zoom,
    filter: ["==", ["get", "stairs"], true],
    paint: { "line-width": 1.5, "line-color": "#182e36", "line-dasharray": [.5, 3] } }, before);
  map.addLayer({ id: "path-hover", type: "line", source: "path-hover",
    paint: { "line-color": "#20bce0", "line-width": 7 } }, before);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 28;
  const ctx = canvas.getContext("2d")!;
  ctx.beginPath(); ctx.moveTo(14, 3); ctx.lineTo(24, 20); ctx.lineTo(14, 16); ctx.lineTo(4, 20); ctx.closePath();
  ctx.fillStyle = "#142b40"; ctx.fill(); ctx.strokeStyle = "#fffdf5"; ctx.lineWidth = 2; ctx.stroke();
  map.addImage("uphill-arrow", ctx.getImageData(0, 0, 28, 28));
  map.addLayer({ id: "path-arrows", type: "symbol", source: "path-arrows", minzoom: config.min_zoom,
    layout: { "icon-image": "uphill-arrow", "icon-size": .65, "icon-rotate": ["get", "bearing"],
      "icon-rotation-alignment": "map", "icon-allow-overlap": true } }, before);
}

function selectedArrows(result: Inspection): FeatureCollection<Point> {
  const features: Feature<Point>[] = [];
  let previous = -Infinity;
  for (const interval of result.intervals) {
    if (!reliable(interval) || Math.abs(interval.grade_percent!) < 1e-6 || interval.start_m - previous < 30) continue;
    previous = interval.start_m;
    let [a, b] = interval.geometry;
    if (interval.grade_percent! < 0) [a, b] = [b, a];
    const bearing = Math.atan2((b[0] - a[0]) * Math.cos(a[1] * Math.PI / 180), b[1] - a[1]) * 180 / Math.PI;
    features.push({ type: "Feature", properties: { bearing }, geometry: { type: "Point", coordinates: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] } });
  }
  return { type: "FeatureCollection", features };
}

function profileChart(result: Inspection, hover: (interval?: Interval) => void): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 360 150");
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", "Estimated terrain height change. Focus or hover a sample to inspect its signed grade.");
  const heights = result.intervals.flatMap(i => i.valid ? [i.relative_start_m!, i.relative_end_m!] : []);
  const low = Math.min(0, ...heights), high = Math.max(1, ...heights);
  const length = result.intervals.at(-1)?.end_m || 1;
  const x = (m: number) => 36 + 314 * m / length;
  const y = (m: number) => 117 - 99 * (m - low) / (high - low);
  for (const [value, label] of [[high, `${high.toFixed(1)} m`], [low, `${low.toFixed(1)} m`]] as const) {
    const node = document.createElementNS(ns, "text");
    node.setAttribute("x", "2"); node.setAttribute("y", String(y(value) + 4)); node.textContent = label; svg.append(node);
  }
  const labeledRuns = new Set<number>();
  for (const interval of result.intervals) {
    const hit = document.createElementNS(ns, "rect");
    hit.setAttribute("x", String(x(interval.start_m))); hit.setAttribute("y", "12");
    hit.setAttribute("width", String(Math.max(1, x(interval.end_m) - x(interval.start_m)))); hit.setAttribute("height", "111");
    hit.setAttribute("fill", interval.valid ? "transparent" : "#d2d5d7");
    hit.setAttribute("tabindex", "0"); hit.setAttribute("role", "img");
    hit.setAttribute("aria-label", `${interval.start_m.toFixed(0)}–${interval.end_m.toFixed(0)} metres: ${interval.valid ? `${interval.grade_percent!.toFixed(1)} percent` : "missing terrain"}`);
    hit.addEventListener("pointerenter", () => hover(interval));
    hit.addEventListener("focus", () => hover(interval));
    hit.addEventListener("blur", () => hover());
    svg.append(hit);
    if (interval.valid) {
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", String(x(interval.start_m))); line.setAttribute("x2", String(x(interval.end_m)));
      line.setAttribute("y1", String(y(interval.relative_start_m!))); line.setAttribute("y2", String(y(interval.relative_end_m!)));
      line.setAttribute("stroke", reliable(interval) ? "#087f8c" : "#8b619e"); line.setAttribute("stroke-width", "2.5");
      line.setAttribute("pointer-events", "none"); svg.append(line);
      if (interval.profile_run! > 0 && !labeledRuns.has(interval.profile_run!)) {
        labeledRuns.add(interval.profile_run!);
        const reset = document.createElementNS(ns, "text");
        reset.setAttribute("x", String(x(interval.start_m))); reset.setAttribute("y", "10"); reset.textContent = "reset"; svg.append(reset);
      }
    }
  }
  const distance = document.createElementNS(ns, "text"); distance.setAttribute("x", "36"); distance.setAttribute("y", "145");
  distance.textContent = `0 → ${length.toFixed(0)} m along path (EPSG:3067)`; svg.append(distance);
  svg.addEventListener("pointerleave", () => hover());
  return svg;
}

function inclusionText(interval: Inclusion): string {
  if (!interval.inclusion_state) return "";
  const reason = interval.inclusion_state === "included" ? "within budget"
    : interval.inclusion_state === "no_connection" ? "no connection in prepared data"
    : interval.inclusion_state === "excluded" ? `excluded: ${interval.inclusion_reason}` : "beyond budget";
  const minimum = interval.minimum_inclusion_m == null ? "" : ` · minimum outing ${(interval.minimum_inclusion_m / 1000).toFixed(3)} km`;
  return ` · ${reason}${minimum}${interval.coverage_limited ? " · coverage limited" : ""}`;
}

function applyPathEmphasis(map: Map, flatter: boolean): void {
  const alpha: ExpressionSpecification = ["case", ["==", ["get", "eligible"], false], .08,
    ["match", ["coalesce", ["get", "inclusion_state"], ""], "beyond_budget", .30, "no_connection", .12, "excluded", .08, 1]];
  const opacity: ExpressionSpecification = ["*", alpha, flatter
    ? ["case", ["all", ["==", ["get", "quality"], "terrain_estimate"], [">=", ["abs", ["coalesce", ["get", "grade_percent"], 0]], 2]], .45, 1] : 1];
  for (const layer of ["path-casing", "path-grades", "path-uncertain", "path-stairs"]) map.setPaintProperty(layer, "line-opacity", opacity);
}

function renderInspection(map: Map, result: Inspection): void {
  const content = element("path-inspection-content");
  const tags = result.segment.tags;
  content.replaceChildren(text("h2", tags.name || "Unnamed segment"),
    text("p", `${tags.highway || "path"} · ${result.segment.length_m.toFixed(0)} m · ${result.surface_class} surface`, "segment-subtitle"));
  if (!result.eligible) content.append(text("p", `Excluded by current eligibility: ${result.eligibility_reason}. Geometry remains available for inspection.`, "quality-note"));
  if (result.outing_label) content.append(text("p", result.outing_label, "explanation"));
  const stats = text("p", `↑ ${result.ascent_m.toFixed(1)} m   ↓ ${result.descent_m.toFixed(1)} m`, "segment-stats");
  content.append(stats, text("p", `${result.totals_complete ? "Complete sampled length" : "Partial estimates"} · ${(100 * result.valid_length_fraction).toFixed(1)}% valid length.`, "explanation"));
  const flags = [...new Set(result.intervals.flatMap(i => i.quality_flags))];
  if (flags.length) {
    const groundNote = flags.some(flag => flag === "bridge" || flag === "tunnel")
      ? " Bridge/tunnel values estimate the ground, not the path deck." : "";
    const areaNote = flags.includes("pedestrian_area_perimeter") ? " Pedestrian areas use mapped perimeters." : "";
    content.append(text("p", `Quality: ${flags.map(flag => flag.replaceAll("_", " ")).join(", ")}.${groundNote}${areaNote}`, "quality-note"));
  }
  const sample = text("p", "Hover or focus the profile to inspect signed grade.", "sample-readout");
  const hover = (interval?: Interval) => {
    setData(map, "path-hover", collection(interval ? [lineFeature(interval.geometry)] : []));
    sample.textContent = interval
      ? `${interval.start_m.toFixed(0)}–${interval.end_m.toFixed(0)} m · ${interval.valid ? `${interval.grade_percent! >= 0 ? "+" : ""}${interval.grade_percent!.toFixed(1)}%` : "missing terrain"}${reliable(interval) ? "" : " · uncertain"}${inclusionText(interval)}`
      : "Hover or focus the profile to inspect signed grade.";
  };
  content.append(text("h3", "Estimated terrain height change"), profileChart(result, hover), sample,
    text("p", "Relative to the start of each valid run. Gaps reset to zero. This is integrated terrain change, not absolute altitude or surveyed path elevation.", "explanation"),
    text("h3", "Uphill sections"));
  if (!result.uphill_sections.length) content.append(text("p", "No positive-grade samples in this direction.", "explanation"));
  const list = document.createElement("ul");
  for (const section of result.uphill_sections) list.append(text("li", `At ${section.start_m.toFixed(0)} m: ${section.length_m.toFixed(0)} m long, +${section.gain_m.toFixed(1)} m, mean ${section.mean_grade_percent.toFixed(1)}%, peak ${section.peak_grade_percent.toFixed(1)}%${section.uncertain ? " (uncertain ground estimate)" : ""}`));
  content.append(list, text("h3", "Surface and access evidence"));
  const evidence = ["surface", "smoothness", "tracktype", "foot", "access", "foot:forward", "foot:backward", "oneway:foot", "bridge", "tunnel", "area"]
    .filter(key => tags[key]).map(key => `${key}=${tags[key]}`);
  content.append(text("p", evidence.length ? evidence.join(" · ") : "No surface or access tags recorded.", "evidence"),
    text("p", `Current traversal: forward ${result.traversal_permissions.forward ? "allowed" : "disallowed"}, backward ${result.traversal_permissions.backward ? "allowed" : "disallowed"}. Inspection direction is independent of traversal permission.`, "explanation"));
  const details = document.createElement("details"); details.append(text("summary", "Measurement source and settings"));
  details.append(text("pre", JSON.stringify({ dataset_id: result.dataset_id, segment_id: result.segment_id, method: result.method, source: result.source }, null, 2)));
  content.append(details);
}

export async function startPaths(map: Map): Promise<void> {
  const status = element("path-status");
  const panel = element("path-inspection");
  const reverse = element<HTMLInputElement>("path-reverse");
  const filter = element<HTMLSelectElement>("path-surface");
  const avoidStairs = element<HTMLInputElement>("path-avoid-stairs");
  const flatter = element<HTMLInputElement>("path-flatter");
  const enabled = element<HTMLInputElement>("outing-enabled");
  const budget = element<HTMLInputElement>("outing-budget");
  const radius = element<HTMLInputElement>("outing-radius");
  const setStart = element<HTMLButtonElement>("outing-set-start");
  const clearStart = element<HTMLButtonElement>("outing-clear-start");
  const outingStatus = element("outing-status");
  const setStatus = (message: string, state: string) => { status.textContent = message; status.dataset.state = state; };
  let config: PathConfig;
  try {
    config = await request<PathConfig>("/api/paths/config");
    if (config.status !== "ready") {
      setStatus(config.status === "failed" ? "Path analysis failed to load. Check prepared artifacts." : "Path analysis unavailable — prepare local measurements.", config.status);
      return;
    }
  } catch {
    setStatus("Path configuration request failed. Terrain remains available.", "failed"); return;
  }
  element("path-settings").textContent = `Path analysis: fixed ${config.method.wavelength_m} m smoothing, ≤${config.method.sampling_interval_m} m samples.`;
  element("path-importer-notes").replaceChildren(...config.importer_notes.map(note => text("li", note)));
  budget.value = String(config.default_budget_km);
  radius.value = String(config.snap_radius_m);
  for (const control of [filter, avoidStairs, flatter, enabled, budget, radius, setStart]) control.disabled = false;
  addPathLayers(map, config);
  applyPathEmphasis(map, flatter.checked);
  map.addSource("start-offset", { type: "geojson", data: empty });
  map.addLayer({ id: "start-offset", type: "line", source: "start-offset",
    paint: { "line-color": "#142b40", "line-width": 2, "line-dasharray": [2, 2] } });
  const requestedMarker = new Marker({ color: "#858d92", scale: .7 });
  const snappedMarker = new Marker({ color: "#142b40", scale: .8 });
  requestedMarker.getElement().title = "Requested start";
  snappedMarker.getElement().title = "Snapped start";
  map.jumpTo({ center: [config.initial_view.longitude, config.initial_view.latitude], zoom: config.initial_view.zoom });
  const originalOpacity = new globalThis.Map<string, number | ExpressionSpecification>(
    contextLayers.filter(id => map.getLayer(id)).map(id => {
      const opacity = map.getPaintProperty(id, "line-opacity");
      return [id, typeof opacity === "number" || Array.isArray(opacity) ? opacity : 1];
    }),
  );
  const context = (analysis: boolean) => {
    for (const [id, opacity] of originalOpacity) map.setPaintProperty(id, "line-opacity", analysis
      ? ["case", ["within", config.coverage.geometry], .12, opacity] : opacity);
  };
  let viewportAbort: AbortController | undefined;
  let inspectionAbort: AbortController | undefined;
  let viewportSequence = 0;
  let inspectionSequence = 0;
  let selected: string | undefined;
  let requestedStart: [number, number] | undefined;
  let choosingStart = false;
  function chooseStartMode(active: boolean): void {
    choosingStart = active;
    setStart.textContent = active ? "Cancel start selection" : "Set start on map";
    setStart.setAttribute("aria-pressed", String(active));
    map.getCanvas().style.cursor = active ? "crosshair" : "";
    if (active) outingStatus.textContent = "Click the map to choose a start. The nearest eligible path within the snap radius will be selected.";
  }
  function showStart(result?: StartResponse): void {
    requestedMarker.remove(); snappedMarker.remove();
    setData(map, "start-offset", empty);
    clearStart.disabled = !requestedStart;
    element("outing-label").textContent = enabled.checked && requestedStart ? `Within a ${Number(budget.value)} km outing — retracing allowed` : "";
    if (!enabled.checked || !requestedStart) return;
    requestedMarker.setLngLat(requestedStart).addTo(map);
    if (!result) return;
    outingStatus.dataset.state = result.snap ? "ready" : "failed";
    if (!result.snap) {
      outingStatus.textContent = `No eligible path within ${radius.value} m. Choose another start or increase the snap radius.`;
      return;
    }
    const snap = result.snap;
    snappedMarker.setLngLat([snap.longitude, snap.latitude]).addTo(map);
    setData(map, "start-offset", collection([lineFeature([requestedStart, [snap.longitude, snap.latitude]])]));
    outingStatus.textContent = `Requested ${requestedStart[1].toFixed(5)}, ${requestedStart[0].toFixed(5)} → snapped ${snap.latitude.toFixed(5)}, ${snap.longitude.toFixed(5)} · ${snap.offset_m.toFixed(1)} m offset.`;
  }
  function parameters(): URLSearchParams {
    return new URLSearchParams({ surface: filter.value, avoid_stairs: String(avoidStairs.checked), dataset_id: config.dataset_id });
  }
  function addOutingParameters(params: URLSearchParams): boolean {
    if (!enabled.checked || !requestedStart) return false;
    params.set("longitude", String(requestedStart[0])); params.set("latitude", String(requestedStart[1]));
    params.set("budget_km", budget.value); params.set("radius_m", radius.value);
    return true;
  }
  async function updateViewport(): Promise<void> {
    viewportAbort?.abort();
    viewportAbort = new AbortController();
    const sequence = ++viewportSequence;
    const bounds = map.getBounds();
    const params = parameters();
    for (const [key, value] of Object.entries({ west: bounds.getWest(), south: bounds.getSouth(), east: bounds.getEast(), north: bounds.getNorth(), zoom: map.getZoom() })) params.set(key, String(value));
    const outing = addOutingParameters(params);
    setStatus(outing ? "Calculating outing inclusion…" : "Loading path measurements…", "loading");
    try {
      const result = await request<Viewport>(`/api/paths/${outing ? "explore" : "viewport"}?${params}`, viewportAbort.signal);
      if (sequence !== viewportSequence) return;
      if (result.dataset_id !== config.dataset_id) throw new Error("Dataset changed — reload the map.");
      setData(map, "path-intervals", result);
      showStart(result.start);
      context(result.status === "ready");
      if (result.status === "outside_coverage") setStatus("Outside prepared path coverage. Basemap paths are context.", "outside_coverage");
      else if (result.status === "zoom_in") setStatus("Zoom in for path gradients. Neutral paths remain context.", "zoom_in");
      else if (result.status === "no_eligible_snap") setStatus("Start could not be snapped to an eligible path.", "no_eligible_snap");
      else setStatus(`${result.features.length.toLocaleString()} intervals${result.coverage_limited ? " · view crosses prepared coverage boundary" : " · click a path to inspect"}`, "ready");
    } catch (error) {
      if (sequence !== viewportSequence || (error as Error).name === "AbortError") return;
      setData(map, "path-intervals", empty); context(false);
      setStatus(`Path request failed. ${(error as Error).message}`, "failed");
    }
  }
  async function inspectSelected(): Promise<void> {
    inspectionAbort?.abort();
    inspectionAbort = new AbortController();
    const sequence = ++inspectionSequence;
    if (!selected) return;
    panel.hidden = false;
    setData(map, "path-selection", empty); setData(map, "path-hover", empty); setData(map, "path-arrows", empty);
    element("path-inspection-content").replaceChildren(text("p", "Loading segment…"));
    const params = parameters();
    params.set("reverse", String(reverse.checked));
    addOutingParameters(params);
    try {
      const result = await request<Inspection>(`/api/paths/segments/${encodeURIComponent(selected)}?${params}`, inspectionAbort.signal);
      if (sequence !== inspectionSequence) return;
      if (result.dataset_id !== config.dataset_id) throw new Error("Dataset changed — reload the map.");
      setData(map, "path-selection", collection([lineFeature(result.segment.geometry)]));
      setData(map, "path-arrows", selectedArrows(result));
      renderInspection(map, result);
    } catch (error) {
      if (sequence !== inspectionSequence || (error as Error).name === "AbortError") return;
      setData(map, "path-selection", empty);
      element("path-inspection-content").replaceChildren(text("p", `Inspection failed. ${(error as Error).message}`));
    }
  }
  function refresh(): void { void updateViewport(); void inspectSelected(); }
  map.on("movestart", () => { viewportAbort?.abort(); viewportSequence++; });
  map.on("moveend", () => { void updateViewport(); });
  for (const input of [filter, avoidStairs]) input.addEventListener("change", () => refresh());
  for (const input of [budget, radius]) input.addEventListener("change", () => { if (input.reportValidity()) refresh(); });
  flatter.addEventListener("change", () => applyPathEmphasis(map, flatter.checked));
  enabled.addEventListener("change", () => {
    if (enabled.checked && !requestedStart) chooseStartMode(true);
    else if (!enabled.checked) chooseStartMode(false);
    showStart(); refresh();
  });
  setStart.addEventListener("click", () => chooseStartMode(!choosingStart));
  clearStart.addEventListener("click", () => {
    requestedStart = undefined; enabled.checked = false;
    chooseStartMode(false); showStart(); outingStatus.textContent = "Choose a start to enable distance highlighting."; refresh();
  });
  map.on("click", event => {
    if (choosingStart) {
      requestedStart = [event.lngLat.lng, event.lngLat.lat]; enabled.checked = true;
      chooseStartMode(false); showStart(); outingStatus.textContent = "Snapping to an eligible path…"; refresh(); return;
    }
    const feature = map.queryRenderedFeatures(event.point, { layers: ["path-hit"] })[0];
    const id = feature?.properties.segment_id as string | undefined;
    if (!id) return;
    selected = id; reverse.checked = false; void inspectSelected();
  });
  map.on("mouseenter", "path-hit", () => { map.getCanvas().style.cursor = choosingStart ? "crosshair" : "pointer"; });
  map.on("mouseleave", "path-hit", () => { map.getCanvas().style.cursor = choosingStart ? "crosshair" : ""; });
  reverse.addEventListener("change", () => { void inspectSelected(); });
  element("path-close").addEventListener("click", () => {
    inspectionAbort?.abort(); inspectionSequence++; selected = undefined; panel.hidden = true;
    for (const source of ["path-selection", "path-hover", "path-arrows"]) setData(map, source, empty);
  });
  await updateViewport();
}

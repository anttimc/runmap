/** Coordinate path layers and distance exploration. */
import {
  Marker,
  type GeoJSONSource,
  type Map,
  type ExpressionSpecification,
  type AddLayerObject,
  type MapMouseEvent,
} from "maplibre-gl";
import type { Feature, FeatureCollection, LineString } from "geojson";
import { ApiError, loadDistanceShells, prepareSnapshot } from "./api";
import {
  addShellLayers,
  distanceBandM,
  distanceBands,
  setShellData,
  showShellLayers,
} from "./distance-shells";
import type {
  ColorStop,
  Location,
  PathConfig,
  SnapshotResponse,
  StartResponse,
  ShellMethod,
} from "./api-types";
import type { SharedState } from "./state";
import { element } from "./dom";

const empty: FeatureCollection = { type: "FeatureCollection", features: [] };
const lowGradePathColor = "#a1d99b";

function renderDistanceLegend(
  maximumKm: number,
  detour = false,
  percentage = false,
  percentageInterval = 1,
): void {
  const maximumM = maximumKm * 1000;
  const intervalM = percentage ? percentageInterval : detour ? 200 : distanceBandM;
  const bands = distanceBands(maximumM, intervalM);
  element("distance-ramp").style.background = `linear-gradient(to right, ${bands
    .map(
      (band) =>
        `${band.color} ${(100 * band.startM) / maximumM}% ${(100 * Math.min(band.startM + intervalM, maximumM)) / maximumM}%`,
    )
    .join(", ")})`;
  const formatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
  element("distance-ticks").textContent = percentage
    ? `0–${formatter.format(maximumM)}% extra travel · each color = ${intervalM} percentage point · higher values use the top color`
    : `0–${formatter.format(maximumKm)} km${detour ? " extra travel" : ""} · each color = ${intervalM} m`;
}

function renderGradientLegend(stops: ColorStop[]): void {
  const maximum = stops[stops.length - 1].percent;
  element("path-gradient-ramp").style.background =
    `linear-gradient(to right, ${stops.map((stop) => `${stop.color} ${(100 * stop.percent) / maximum}%`).join(", ")})`;
  const labeledStops = stops.filter((stop, index) => index === 0 || stop.percent / maximum >= 0.25);
  element("path-gradient-ticks").replaceChildren(
    ...labeledStops.map((stop, index) => {
      const label = text("span", `${stop.percent}%`);
      label.style.left = `${(100 * stop.percent) / maximum}%`;
      label.className = "ramp-tick";
      if (index === 0) label.classList.add("ramp-tick-first");
      if (index === labeledStops.length - 1) label.classList.add("ramp-tick-last");
      return label;
    }),
  );
}

function gradientExpression(stops: ColorStop[]): ExpressionSpecification {
  return [
    "interpolate",
    ["linear"],
    ["abs", ["coalesce", ["get", "grade_percent"], 0]],
    ...stops.flatMap((stop) => [stop.percent, stop.color]),
  ] as ExpressionSpecification;
}
const contextLayers = [
  "runner-path-casing",
  "runner-paths-maintained",
  "runner-paths-unmaintained",
  "runner-pedestrian-roads",
];

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
function addPathLayers(map: Map, config: PathConfig, stops: ColorStop[]): void {
  const before = map.getStyle().layers?.find((layer) => layer.type === "symbol")?.id;
  map.addSource("path-coverage", {
    type: "geojson",
    data: { type: "Feature", properties: {}, geometry: config.coverage.geometry },
  });
  map.addLayer(
    {
      id: "path-coverage",
      type: "line",
      source: "path-coverage",
      paint: { "line-color": "#536773", "line-width": 1.5, "line-dasharray": [4, 3] },
    },
    before,
  );
  map.addSource("path-intervals", {
    type: "vector",
    tiles: [
      config.tile_template
        .replace("{policy}", "all-no-stairs")
        .replace("{variant}", "gradient-hidden"),
    ],
    bounds: config.bounds_wgs84,
    minzoom: config.tile_min_zoom,
    maxzoom: config.tile_max_zoom,
  });
  for (const [id, color, scale] of [
    ["path-flat-halo", "#fffdf8", 3.5],
    ["path-flat-outline", "#238443", 2.5],
  ] as const) {
    map.addLayer(
      {
        id,
        type: "line",
        source: "path-intervals",
        "source-layer": "paths",
        minzoom: config.tile_min_zoom,
        layout: { visibility: "none", "line-cap": "round", "line-join": "round" },
        paint: { "line-color": color, "line-width": pathWidth(["literal", scale]) },
      },
      before,
    );
  }
  map.addLayer(
    {
      id: "path-casing",
      type: "line",
      source: "path-intervals",
      "source-layer": "paths",
      minzoom: config.tile_min_zoom,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#a6a6a6",
        "line-width": pathWidth(["literal", 1], true),
        "line-opacity": pathCasingOpacity(),
      },
    },
    before,
  );
  const gradeColor: ExpressionSpecification = gradientExpression(stops);
  map.addLayer(
    {
      id: "path-grades",
      type: "line",
      source: "path-intervals",
      "source-layer": "paths",
      minzoom: config.tile_min_zoom,
      filter: ["==", ["get", "quality"], "terrain_estimate"],
      layout: { "line-cap": "butt", "line-join": "round" },
      paint: {
        "line-width": pathWidth(["literal", 1]),
        "line-color": gradeColor,
      },
    },
    before,
  );
  map.addLayer(
    {
      id: "path-uncertain",
      type: "line",
      source: "path-intervals",
      "source-layer": "paths",
      minzoom: config.tile_min_zoom,
      filter: ["!=", ["get", "quality"], "terrain_estimate"],
      paint: {
        "line-width": pathWidth(["literal", 1]),
        "line-dasharray": [1.5, 1],
        "line-color": ["match", ["get", "quality"], "missing", "#727980", "#8b619e"],
      },
    },
    before,
  );
  map.addLayer(
    {
      id: "path-stairs",
      type: "line",
      source: "path-intervals",
      "source-layer": "paths",
      minzoom: config.tile_min_zoom,
      filter: ["==", ["get", "stairs"], true],
      paint: {
        "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.4, 12, 0.7, 14, 1.5],
        "line-color": "#182e36",
        "line-dasharray": [0.5, 3],
      },
    },
    before,
  );
}

function pathWidth(scale: ExpressionSpecification, casing = false): ExpressionSpecification {
  return [
    "interpolate",
    ["linear"],
    ["zoom"],
    10,
    ["*", casing ? 0.65 : 0.4, scale],
    12,
    ["*", casing ? 1 : 0.7, scale],
    14,
    ["*", casing ? 2.8 : 1.8, scale],
    15,
    ["*", casing ? 3.2 : 2.2, scale],
  ];
}

function pathCasingOpacity(opacity: ExpressionSpecification | number = 1): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], 13, 0, 14, opacity];
}

function applyPathEmphasis(map: Map, highlightFlat: boolean, threshold: number): void {
  const alpha: ExpressionSpecification = ["case", ["==", ["get", "eligible"], false], 0.08, 1];
  const measured: ExpressionSpecification = [
    "all",
    ["==", ["get", "quality"], "terrain_estimate"],
    ["!=", ["get", "grade_percent"], null],
  ];
  const flat: ExpressionSpecification = [
    "all",
    measured,
    ["!=", ["get", "eligible"], false],
    ["has", "flat_min_grade_50m"],
    ["<=", ["coalesce", ["get", "flat_min_grade_50m"], 11], threshold],
  ];
  for (const layer of ["path-uncertain", "path-stairs"])
    map.setPaintProperty(layer, "line-opacity", alpha);
  map.setPaintProperty("path-grades", "line-opacity", alpha);
  map.setPaintProperty("path-casing", "line-opacity", pathCasingOpacity(alpha));
  for (const layer of ["path-flat-halo", "path-flat-outline"]) {
    map.setFilter(layer, flat);
    map.setLayoutProperty(layer, "visibility", highlightFlat ? "visible" : "none");
  }
}

export interface PathControls {
  modeChanged: () => void;
  homeChanged: () => void;
  policyChanged: () => void;
  click: (event: MapMouseEvent) => void;
  picking: () => boolean;
}

interface Options {
  state: Readonly<SharedState>;
  setHome: (location: Location | undefined) => void;
  plannerPicking: () => boolean;
}

export function startPaths(
  map: Map,
  slopeStops: ColorStop[],
  config: PathConfig,
  options: Options,
): PathControls {
  const stops = slopeStops
    .filter((stop) => stop.percent === 0 || stop.percent >= 10)
    .map((stop) => (stop.percent === 0 ? { ...stop, color: lowGradePathColor } : stop));
  const status = element("path-status");
  const pathGradient = element<HTMLInputElement>("layer-path");
  const backgroundGradient = element<HTMLInputElement>("layer-background");
  const distanceLayer = element<HTMLInputElement>("layer-distance");
  const hideBuildingAccess = element<HTMLInputElement>("path-hide-building-access");
  const flatter = element<HTMLInputElement>("path-flatter");
  const flatThreshold = element<HTMLInputElement>("path-flat-threshold");
  const budget = element<HTMLInputElement>("outing-budget");
  const setStart = element<HTMLButtonElement>("outing-set-start");
  const clearStart = element<HTMLButtonElement>("outing-clear-start");
  const outingStatus = element("outing-status");
  const shellMethod = element<HTMLSelectElement>("distance-shell-method");
  const subtractStraightLine = element<HTMLInputElement>("distance-subtract-straight-line");
  const networkDistance = element<HTMLInputElement>("distance-network");
  const smoothing = element<HTMLSelectElement>("distance-smoothing");
  const extraPercentage = element<HTMLInputElement>("distance-extra-percentage");
  const extraTravelEnabled = (): boolean => subtractStraightLine.checked || extraPercentage.checked;
  const shellStatus = element("distance-shell-status");
  const setStatus = (message: string, state: string) => {
    status.textContent = message;
    status.dataset.state = state;
  };
  element("path-settings").textContent =
    `Path analysis: fixed ${config.method.wavelength_m} m smoothing, ≤${config.method.sampling_interval_m} m samples.`;
  element("path-importer-notes").replaceChildren(
    ...config.importer_notes.map((note) => text("li", note)),
  );
  budget.value = String(config.default_budget_km);
  for (const control of [
    pathGradient,
    distanceLayer,
    hideBuildingAccess,
    flatter,
    flatThreshold,
    budget,
    setStart,
  ])
    control.disabled = false;
  addPathLayers(map, config, stops);
  addShellLayers(map);
  distanceLayer.disabled = !config.shell_methods?.length;
  distanceLayer.title = distanceLayer.disabled
    ? "Distance shells require prepared numeric routing data."
    : "";
  shellMethod.disabled = distanceLayer.disabled;
  subtractStraightLine.disabled = distanceLayer.disabled;
  networkDistance.disabled = distanceLayer.disabled;
  extraPercentage.disabled = distanceLayer.disabled;
  if (!config.shell_methods?.length)
    shellStatus.textContent = "Shell display is unavailable for this dataset.";
  renderGradientLegend(stops);
  applyPathEmphasis(map, flatter.checked, Number(flatThreshold.value));
  map.addSource("start-offset", { type: "geojson", data: empty });
  map.addLayer({
    id: "start-offset",
    type: "line",
    source: "start-offset",
    paint: { "line-color": "#142b40", "line-width": 2, "line-dasharray": [2, 2] },
  });
  const requestedMarker = new Marker({ color: "#858d92", scale: 0.7 });
  const snappedMarker = new Marker({ color: "#142b40", scale: 0.8 });
  requestedMarker.getElement().title = "Requested start";
  snappedMarker.getElement().title = "Snapped start";
  map.jumpTo({
    center: [config.initial_view.longitude, config.initial_view.latitude],
    zoom: config.initial_view.zoom,
  });
  const originalOpacity = new globalThis.Map<string, number | ExpressionSpecification>(
    contextLayers
      .filter((id) => map.getLayer(id))
      .map((id) => {
        const opacity = map.getPaintProperty(id, "line-opacity");
        return [id, typeof opacity === "number" || Array.isArray(opacity) ? opacity : 1];
      }),
  );
  const context = (analysis: boolean) => {
    for (const [id, opacity] of originalOpacity)
      map.setPaintProperty(
        id,
        "line-opacity",
        analysis ? ["case", ["within", config.coverage.geometry], 0.12, opacity] : opacity,
      );
  };
  let viewportAbort: AbortController | undefined;
  let viewportSequence = 0;
  let activeSource = "path-intervals";
  let activeTemplate = config.tile_template
    .replace("{policy}", "all-no-stairs")
    .replace("{variant}", "gradient-hidden");
  let sourceGeneration = 0;
  let snapshotKey = "";
  let currentSnapshot: SnapshotResponse | undefined;
  let expiryRetried = false;
  let preparing = false;
  let pendingView = "";
  let installedShell = "";
  let shellMaximumM = 1;
  let shellInterval = 1;
  let tileFailed = false;
  const distanceEnabled = (): boolean => distanceLayer.checked;
  const highlightFlat = (): boolean => backgroundGradient.checked && flatter.checked;
  const showPathAnalysis = (): boolean => pathGradient.checked || highlightFlat();
  let choosingStart = false;
  function chooseStartMode(active: boolean): void {
    if (active) document.dispatchEvent(new Event("distancepick"));
    choosingStart = active;
    setStart.textContent = active ? "Cancel start selection" : "Set start on map";
    setStart.setAttribute("aria-pressed", String(active));
    map.getCanvas().style.cursor = active ? "crosshair" : "";
    if (active)
      outingStatus.textContent =
        "Click the map to choose a start. The nearest eligible path within the snap radius will be selected.";
  }
  function showStart(result?: StartResponse): void {
    requestedMarker.remove();
    snappedMarker.remove();
    setData(map, "start-offset", empty);
    clearStart.disabled = !options.state.home;
    element("outing-label").textContent =
      distanceEnabled() && options.state.home
        ? `${Number(budget.value)} km forward distance · return not included`
        : "";
    if (!distanceEnabled() || !options.state.home) return;
    requestedMarker.setLngLat(options.state.home).addTo(map);
    if (!result) return;
    outingStatus.dataset.state = result.snap ? "ready" : "failed";
    if (!result.snap) {
      outingStatus.textContent = `No eligible path within ${String(options.state.policy.radius_m)} m. Choose another start or increase the snap radius.`;
      return;
    }
    const snap = result.snap;
    snappedMarker.setLngLat([snap.longitude, snap.latitude]).addTo(map);
    setData(
      map,
      "start-offset",
      collection([lineFeature([options.state.home, [snap.longitude, snap.latitude]])]),
    );
    outingStatus.textContent = `Requested ${options.state.home[1].toFixed(5)}, ${options.state.home[0].toFixed(5)} → snapped ${snap.latitude.toFixed(5)}, ${snap.longitude.toFixed(5)} · ${snap.offset_m.toFixed(1)} m offset.`;
  }
  function parameters(): URLSearchParams {
    return new URLSearchParams({
      surface: options.state.policy.surface,
      avoid_stairs: String(options.state.policy.avoid_stairs),
      hide_building_access: String(hideBuildingAccess.checked),
      dataset_id: config.dataset_id,
    });
  }
  function addOutingParameters(params: URLSearchParams): boolean {
    if (!distanceEnabled() || !options.state.home) return false;
    params.set("longitude", String(options.state.home[0]));
    params.set("latitude", String(options.state.home[1]));
    params.set("budget_km", budget.value);
    params.set("radius_m", String(options.state.policy.radius_m));
    return true;
  }
  function installTiles(template?: string): void {
    const nextTemplate =
      template ||
      config.tile_template
        .replace("{policy}", policy())
        .replace("{variant}", "gradient-hidden")
        .replace("{z}", "0")
        .replace("{x}", "0")
        .replace("{y}", "0");
    if (nextTemplate === activeTemplate && !tileFailed) return;
    const oldSource = activeSource;
    activeSource = `path-tiles-${++sourceGeneration}`;
    map.addSource(activeSource, {
      type: "vector",
      tiles: [nextTemplate],
      bounds: config.bounds_wgs84,
      minzoom: config.tile_min_zoom,
      maxzoom: config.tile_max_zoom,
    });
    const layers = map.getStyle().layers || [];
    for (let i = 0; i < layers.length; i++) {
      const layer = layers[i];
      if (!("source" in layer) || layer.source !== oldSource) continue;
      const before = layers[i + 1]?.id;
      map.removeLayer(layer.id);
      map.addLayer({ ...layer, source: activeSource } as AddLayerObject, before);
    }
    map.removeSource(oldSource);
    activeTemplate = nextTemplate;
    tileFailed = false;
  }
  function variant(): string {
    return `gradient-${hideBuildingAccess.checked ? "hidden" : "all"}`;
  }
  function policy(): string {
    return `${options.state.policy.surface}-${options.state.policy.avoid_stairs ? "no-stairs" : "stairs"}`;
  }
  function updateLodLegend(): void {
    const zoom = Math.floor(map.getZoom());
    const note = element("path-lod-note");
    note.hidden = !pathGradient.checked || zoom < config.tile_min_zoom || zoom > 14;
    note.textContent = `Zoomed-out grade colors use ${zoom <= 12 ? 2 : 1}% bins, rounded up. `;
  }
  function coverageStatus(): void {
    updateLodLegend();
    updateLegendVisibility();
    updateMetricDescription();
    if (!distanceEnabled() && map.getZoom() < config.tile_min_zoom) {
      setStatus("Zoom in to see prepared paths and flat stretches.", "zoom_in");
      context(false);
      return;
    }
    if (preparing || tileFailed) return;
    if (distanceEnabled()) {
      context(pathGradient.checked);
      if (options.state.home && !currentSnapshot) return;
      const loaded = !!installedShell && map.isSourceLoaded("distance-shells");
      setStatus(
        !options.state.home
          ? "Choose a start to display distance shells."
          : loaded
            ? "Distance shells ready."
            : "Drawing distance shells…",
        !options.state.home ? "choose_start" : loaded ? "ready" : "loading",
      );
      return;
    }
    const bounds = map.getBounds();
    const [w, s, e, n] = config.bounds_wgs84;
    if (
      bounds.getEast() < w ||
      bounds.getWest() > e ||
      bounds.getNorth() < s ||
      bounds.getSouth() > n
    ) {
      setStatus("Outside prepared path coverage. Basemap paths are context.", "outside_coverage");
      context(false);
    } else {
      context(pathGradient.checked);
      const crosses =
        bounds.getWest() < w ||
        bounds.getEast() > e ||
        bounds.getSouth() < s ||
        bounds.getNorth() > n;
      setStatus(
        map.isSourceLoaded(activeSource)
          ? `Prepared paths ready${crosses ? " · view crosses prepared coverage boundary" : ""}`
          : "Loading path tiles…",
        map.isSourceLoaded(activeSource) ? "ready" : "loading",
      );
    }
  }
  async function updateViewport(): Promise<void> {
    installTiles(
      config.tile_template.replace("{policy}", policy()).replace("{variant}", variant()),
    );
    const params = parameters();
    const outing = addOutingParameters(params);
    // Shell methods and visibility never invalidate the distance search.
    params.delete("hide_building_access");
    const key = outing ? params.toString() : "";
    const viewKey = `${key}|${distanceEnabled() ? `${shellMethod.value}|${extraTravelEnabled()}|${extraPercentage.checked}|${smoothing.value}` : variant()}`;
    if (outing && key === snapshotKey && preparing && pendingView === viewKey) return;
    pendingView = viewKey;
    viewportAbort?.abort();
    viewportAbort = new AbortController();
    const sequence = ++viewportSequence;
    const clearShells = () => {
      installedShell = "";
      setShellData(map);
      shellStatus.textContent = "";
    };
    if (!outing) {
      preparing = false;
      currentSnapshot = undefined;
      snapshotKey = "";
      clearShells();
      showStart();
      coverageStatus();
      return;
    }
    if (key !== snapshotKey) expiryRetried = false;
    const reusable =
      key === snapshotKey && currentSnapshot && currentSnapshot.expires_at * 1000 > Date.now();
    if (!reusable) {
      snapshotKey = key;
      currentSnapshot = undefined;
    }
    preparing = true;
    tileFailed = false;
    if (installedShell !== viewKey) clearShells();
    context(false);
    showStart();
    setStatus("Calculating distances from this start…", "loading");
    try {
      const result =
        currentSnapshot ??
        (await prepareSnapshot(
          {
            dataset_id: config.dataset_id,
            longitude: options.state.home![0],
            latitude: options.state.home![1],
            ...options.state.policy,
            budget_km: Number(budget.value),
          },
          viewportAbort.signal,
        ));
      if (sequence !== viewportSequence) return;
      if (result.dataset_id !== config.dataset_id)
        throw new Error("Dataset changed — reload the map.");
      if (result.status === "no_eligible_snap") {
        preparing = false;
        showStart({ status: result.status, snap: null, requested: options.state.home! });
        setStatus("Start could not be snapped to an eligible path.", "no_eligible_snap");
        return;
      }
      currentSnapshot = result;
      if (installedShell !== viewKey) {
        shellStatus.textContent = "Generating distance shells…";
        const method = shellMethod.value as ShellMethod;
        const shells = await loadDistanceShells(
          config.dataset_id,
          result.snapshot_id,
          method,
          viewportAbort.signal,
          extraTravelEnabled(),
          Number(smoothing.value),
          extraPercentage.checked,
        );
        if (sequence !== viewportSequence) return;
        if (
          shells.dataset_id !== config.dataset_id ||
          shells.snapshot_id !== result.snapshot_id ||
          shells.method !== method ||
          shells.subtract_straight_line !== extraTravelEnabled() ||
          shells.extra_travel_percentage !== extraPercentage.checked ||
          shells.smoothing_m !== Number(smoothing.value)
        )
          throw new Error("Distance shell response changed — try again.");
        shellMaximumM = shells.maximum;
        shellInterval = shells.interval;
        setShellData(map, shells);
        showShellLayers(map, true, shellMaximumM, shells.interval_m);
        renderDistanceLegend(
          shellMaximumM / 1000,
          extraTravelEnabled(),
          extraPercentage.checked,
          shellInterval,
        );
        installedShell = viewKey;
        const label = shells.method_used === "grid" ? "Grid" : "Filtered triangulation";
        shellStatus.textContent = `${label} · ${shells.features.length} bands · ${(shells.generation_ms / 1000).toFixed(2)} s${shells.method_used !== method ? " · sparse network fallback" : ""}`;
      }
      preparing = false;
      showStart(result.start);
      outingStatus.textContent += ` Prepared in ${(result.preparation_ms / 1000).toFixed(2)} s.`;
      coverageStatus();
    } catch (error) {
      if (sequence !== viewportSequence || (error as Error).name === "AbortError") return;
      preparing = false;
      clearShells();
      if (error instanceof ApiError && error.status === 410 && !expiryRetried) {
        expiryRetried = true;
        currentSnapshot = undefined;
        void updateViewport();
        return;
      }
      tileFailed = true;
      context(false);
      setStatus(`Path request failed. ${(error as Error).message}`, "failed");
    }
  }
  map.on("sourcedata", (event) => {
    if (event.sourceId === activeSource || event.sourceId === "distance-shells") coverageStatus();
  });
  map.on("error", (event) => {
    const error = event.error as Error & { status?: number; url?: string };
    const sourceId = (event as unknown as { sourceId?: string }).sourceId;
    const isCurrent = sourceId === (distanceEnabled() ? "distance-shells" : activeSource);
    if (!isCurrent || preparing || (!distanceEnabled() && map.getZoom() < config.tile_min_zoom))
      return;
    tileFailed = true;
    setStatus(`Path tile failed to load. ${error.message}`, "failed");
  });
  function refresh(): void {
    updateMetric();
    void updateViewport();
  }
  map.on("moveend", coverageStatus);
  hideBuildingAccess.addEventListener("change", refresh);
  budget.addEventListener("change", () => {
    if (budget.reportValidity()) refresh();
  });
  shellMethod.addEventListener("change", refresh);
  subtractStraightLine.addEventListener("change", refresh);
  networkDistance.addEventListener("change", refresh);
  extraPercentage.addEventListener("change", refresh);
  smoothing.addEventListener("change", refresh);
  function updateMetric(): void {
    updateLodLegend();
    const distance = distanceEnabled();
    const analysis = showPathAnalysis();
    for (const id of ["path-casing", "path-grades", "path-uncertain", "path-stairs"])
      map.setLayoutProperty(id, "visibility", pathGradient.checked ? "visible" : "none");
    map.setLayoutProperty("path-coverage", "visibility", analysis ? "visible" : "none");
    showShellLayers(
      map,
      distance,
      extraTravelEnabled() ? shellMaximumM : Number(budget.value) * 1000,
      extraPercentage.checked ? shellInterval : subtractStraightLine.checked ? 200 : distanceBandM,
    );
    if (!distance) setShellData(map);
    const threshold = Number(flatThreshold.value);
    element("flat-threshold-controls").hidden = !flatter.checked;
    element("path-flat-threshold-value").textContent = `${threshold}%`;
    element("flat-legend-value").textContent = `≤${threshold}% · at least 50 m`;
    renderDistanceLegend(
      extraTravelEnabled() ? shellMaximumM / 1000 : Number(budget.value),
      extraTravelEnabled(),
      extraPercentage.checked,
      shellInterval,
    );
    applyPathEmphasis(map, highlightFlat(), threshold);
    updateLegendVisibility();
    updateMetricDescription();
    coverageStatus();
  }
  function updateMetricDescription(): void {
    smoothing.disabled = distanceLayer.disabled || shellMethod.value !== "grid";
    element("distance-smoothing-note").textContent =
      shellMethod.value === "grid"
        ? "Softens Grid contours. Does not change path distances."
        : "Smoothing applies only if triangulation falls back to Grid.";
    const extraTravel = extraTravelEnabled();
    element("distance-legend-heading").textContent = extraTravel
      ? extraPercentage.checked
        ? "Extra travel (%)"
        : "Extra travel (distance)"
      : "Distance from start";
    element("distance-legend").setAttribute(
      "aria-label",
      extraTravel ? "Extra travel beyond straight-line distance" : "Network distance from start",
    );
    const overview = map.getZoom() < config.tile_min_zoom;
    element("mode-description").textContent = overview
      ? "Zoom in to see colors showing steepness along paths."
      : "Colors show steepness along paths. Click a planned route to explore elevation.";
    element("metric-note").textContent = [
      pathGradient.checked
        ? "Path colors show grade magnitude along paths."
        : "Paths use the neutral map styling.",
      backgroundGradient.checked
        ? "Background colors show terrain slope. A path following a hillside contour can be flat. Terrain colors are softened while distance shading is on."
        : "",
      distanceEnabled()
        ? `Filled colors show ${extraPercentage.checked ? `${shellInterval} percentage point` : subtractStraightLine.checked ? "200 m" : "500 m"} bands of ${extraTravelEnabled() ? "extra travel beyond straight-line distance" : "shortest legal network distance"}. Areas between paths are approximate and do not imply off-path access. Return distance is not included.`
        : "",
      highlightFlat()
        ? `Green outlines mark paths containing stretches of at least 50 m with grades up to ${Number(flatThreshold.value)}%.`
        : "",
    ]
      .filter(Boolean)
      .join(" ");
    flatter.disabled = !backgroundGradient.checked;
    flatThreshold.disabled = !backgroundGradient.checked;
  }
  function updateLegendVisibility(): void {
    const bounds = map.getBounds();
    const [west, south, east, north] = config.bounds_wgs84;
    const covered =
      bounds.getEast() >= west &&
      bounds.getWest() <= east &&
      bounds.getNorth() >= south &&
      bounds.getSouth() <= north;
    const visible = covered && map.getZoom() >= config.tile_min_zoom;
    element("path-legend-title").hidden = !visible || !pathGradient.checked;
    element("gradient-legend").hidden = !visible || !pathGradient.checked;
    element("distance-legend").hidden = !covered || !distanceEnabled();
    element("flat-legend").hidden = !visible || !highlightFlat();
  }
  pathGradient.addEventListener("change", updateMetric);
  backgroundGradient.addEventListener("change", updateMetric);
  distanceLayer.addEventListener("change", () => {
    const needsStart = distanceEnabled() && !options.state.home;
    if (needsStart && element("distance-controls").hidden) element("distance-section").click();
    chooseStartMode(needsStart);
    showStart();
    refresh();
  });
  flatter.addEventListener("change", updateMetric);
  flatThreshold.addEventListener("input", updateMetric);
  setStart.addEventListener("click", () => chooseStartMode(!choosingStart));
  clearStart.addEventListener("click", () => {
    chooseStartMode(false);
    options.setHome(undefined);
    outingStatus.textContent = "Choose a start to enable distance highlighting.";
  });
  function click(event: MapMouseEvent): void {
    if (choosingStart) {
      chooseStartMode(false);
      options.setHome([event.lngLat.lng, event.lngLat.lat]);
      return;
    }
  }
  updateMetric();
  void updateViewport();
  return {
    picking: () => choosingStart,
    modeChanged: () => {
      chooseStartMode(false);
    },
    homeChanged: refresh,
    policyChanged: refresh,
    click,
  };
}

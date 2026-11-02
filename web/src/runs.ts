/** Coordinate planner inputs, endpoint editing, requests, and displayed routes. */
import {
  LngLatBounds,
  Marker,
  type GeoJSONSource,
  type Map,
  type MapMouseEvent,
  type MapTouchEvent,
} from "maplibre-gl";
import type { FeatureCollection, Feature, LineString } from "geojson";
import { startRouteProfile } from "./route-profile";
import { mapPadding } from "./layout";
import { requestRun, snapEndpoint } from "./api";
import type {
  Location,
  PreferenceLevel,
  RunPlannerConfiguration,
  RunResponse,
  RunMode,
  Snap,
} from "./api-types";
import type { SharedState } from "./state";
import { element } from "./dom";
import { candidateName, candidateDetails, runCoordinates } from "./run-results";
import { startTurnaroundRegion } from "./turnaround";

const hillPresets: { level: PreferenceLevel; label: string; description: string }[] = [
  { level: "off", label: "Off", description: "No preference for flatter paths." },
  {
    level: "low",
    label: "Low",
    description: "Prefer gentle climbs, with some allowance for longer routes.",
  },
  {
    level: "high",
    label: "High",
    description: "Strongly avoid steep climbs, allowing longer, gentler routes.",
  },
];
export interface RunControls {
  modeChanged: () => void;
  homeChanged: () => void;
  policyChanged: () => void;
  picking: () => boolean;
  click: (location: Location, point: MapMouseEvent["point"]) => boolean;
}
interface Options {
  datasetId: string;
  turnaroundRegion?: boolean;
  runPlanner?: RunPlannerConfiguration;
  state: Readonly<SharedState>;
  setHome: (location: Location | undefined) => void;
}
const empty: FeatureCollection = { type: "FeatureCollection", features: [] };
const line = (coordinates: Location[], kind: string): Feature<LineString> => ({
  type: "Feature",
  properties: { kind },
  geometry: { type: "LineString", coordinates },
});

function separation(a: Location, b: Location): number {
  const radians = Math.PI / 180;
  return (
    6371000 *
    2 *
    Math.asin(
      Math.min(
        1,
        Math.sqrt(
          Math.sin(((b[1] - a[1]) * radians) / 2) ** 2 +
            Math.cos(a[1] * radians) *
              Math.cos(b[1] * radians) *
              Math.sin(((b[0] - a[0]) * radians) / 2) ** 2,
        ),
      ),
    )
  );
}

function circlePoint(midpoint: Location, radius: number, angle: number): Location {
  return [
    midpoint[0] + (radius * Math.cos(angle)) / (111195 * Math.cos((midpoint[1] * Math.PI) / 180)),
    midpoint[1] + (radius * Math.sin(angle)) / 111195,
  ];
}

function circleCoordinates(midpoint: Location, radius: number): Location[] {
  return Array.from({ length: 65 }, (_, i) =>
    circlePoint(midpoint, radius, (i * 2 * Math.PI) / 64),
  );
}

function addRunLayers(map: Map): void {
  map.addSource("run-geometry", { type: "geojson", data: empty });
  map.addLayer({
    id: "run-guides",
    type: "line",
    source: "run-geometry",
    filter: ["==", ["get", "kind"], "guide"],
    paint: { "line-color": "#55636a", "line-width": 1.5, "line-dasharray": [3, 3] },
  });
  map.addLayer({
    id: "run-route",
    type: "line",
    source: "run-geometry",
    filter: ["==", ["get", "kind"], "route"],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#2367c0", "line-width": 1.5, "line-gap-width": 6 },
  });
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 32;
  const context = canvas.getContext("2d")!;
  context.beginPath();
  context.moveTo(11, 7);
  context.lineTo(21, 16);
  context.lineTo(11, 25);
  context.lineJoin = context.lineCap = "round";
  context.strokeStyle = "#fff";
  context.lineWidth = 7;
  context.stroke();
  context.strokeStyle = "#2367c0";
  context.lineWidth = 3.5;
  context.stroke();
  map.addImage("run-direction", context.getImageData(0, 0, 32, 32));
  map.addLayer({
    id: "run-directions",
    type: "symbol",
    source: "run-geometry",
    filter: ["==", ["get", "kind"], "route"],
    layout: {
      "symbol-placement": "line",
      "symbol-spacing": 95,
      "icon-image": "run-direction",
      "icon-size": 0.65,
      "icon-rotation-alignment": "map",
      "icon-keep-upright": false,
      "icon-padding": 8,
      "symbol-z-order": "source",
    },
  });
}

export function startRunPlanner(map: Map, options: Options): RunControls {
  const active = (): boolean => options.state.plannerMode !== "off";
  let routeMode: RunMode = "round_trip";
  const roundTrip = (): boolean => routeMode === "round_trip";
  const endpointLabel = (): string => (roundTrip() ? "turnaround" : "destination");
  let choosing: "home" | "turnaround" | undefined;
  let turnaround: Location | undefined;
  let dragging = false;
  let response: RunResponse | undefined;
  let responseInputs: string | undefined;
  let selected = 0;
  let generating = false;
  let abort: AbortController | undefined;
  let sequence = 0;
  let snappingAbort: AbortController | undefined;
  let snapSequence = 0;
  let homeSnap: Snap | null = null;
  let turnaroundSnap: Snap | null = null;
  let pickStatus = "";
  const status = element("run-status");
  const avoidance = element<HTMLInputElement>("run-avoidance");
  const hills = element<HTMLInputElement>("run-hills");
  const orientation = element<HTMLSelectElement>("run-orientation");
  const hillCapability = options.runPlanner?.preference_capabilities.hills;
  const supportedHillLevels = hillCapability?.supported_levels ?? ["off"];
  const availableHillPresets = hillPresets.filter((preset) =>
    supportedHillLevels.includes(preset.level),
  );
  if (availableHillPresets.length === 0) availableHillPresets.push(hillPresets[0]);
  hills.max = String(availableHillPresets.length - 1);
  hills.value = String(
    Math.max(
      0,
      availableHillPresets.findIndex(
        (preset) => preset.level === options.runPlanner?.preference_defaults.hills,
      ),
    ),
  );
  hills.disabled = availableHillPresets.length <= 1;
  element("run-hills-labels").replaceChildren(
    ...availableHillPresets.map((preset) => {
      const label = document.createElement("span");
      label.textContent = preset.label;
      return label;
    }),
  );
  const hillPreset = (): (typeof hillPresets)[number] => availableHillPresets[Number(hills.value)];
  function updateHillPreset(): void {
    const preset = hillPreset();
    element("run-hills-value").textContent = preset.label;
    element("run-hills-description").textContent = preset.description;
    hills.setAttribute("aria-valuetext", preset.label);
  }
  updateHillPreset();
  let regionSelectionReady = false;
  const refreshRegion = startTurnaroundRegion(map, {
    datasetId: options.datasetId,
    supported: options.turnaroundRegion ?? false,
    state: options.state,
    width: () => Number(avoidance.value),
    hills: () => hillPreset().level !== "off",
    selectionChanged: (ready) => {
      regionSelectionReady = ready;
      if (choosing === "home") return;
      pick(ready ? "turnaround" : undefined);
    },
  });
  element("run-preferences").hidden = hills.disabled;
  const inputsKey = (): string =>
    JSON.stringify([
      routeMode,
      options.state.home,
      turnaround,
      options.state.policy,
      roundTrip() ? avoidance.value : "0",
      hills.value,
    ]);
  let observedInputs = inputsKey();
  const markers = [
    new Marker({ color: "#193c38", draggable: true }),
    new Marker({ color: "#805523", draggable: true }),
  ];
  markers[0].getElement().title = "Start";
  markers[1].getElement().title = "Turnaround";
  const handle = document.createElement("button");
  handle.type = "button";
  handle.className = "run-centre-handle";
  handle.textContent = "↔";
  handle.title = "Drag to resize loop width";
  handle.setAttribute("aria-label", "Loop width: drag to resize");
  const centreMarker = new Marker({ element: handle, draggable: true });
  addRunLayers(map);
  const profile = startRouteProfile(map, options.datasetId);
  map.addLayer({
    id: "run-centre-circle",
    type: "line",
    source: "run-geometry",
    filter: ["==", ["get", "kind"], "centre"],
    paint: { "line-color": "#95602b", "line-width": 4, "line-opacity": 0.7 },
  });
  map.addLayer({
    id: "run-centre-hit",
    type: "line",
    source: "run-geometry",
    filter: ["==", ["get", "kind"], "centre"],
    paint: { "line-width": 20, "line-opacity": 0 },
  });
  let displayFeatures: Feature<LineString>[] = [];

  function draw(): void {
    refreshRegion();
    const home = options.state.home;
    const a: Location | undefined = homeSnap ? [homeSnap.longitude, homeSnap.latitude] : home;
    const b: Location | undefined = turnaroundSnap
      ? [turnaroundSnap.longitude, turnaroundSnap.latitude]
      : turnaround;
    const features: Feature<LineString>[] = [];
    centreMarker.remove();
    // Keep endpoint marker DOM attached while MapLibre is handling its drag.
    // A concurrent state update can call draw() between drag events; removing
    // the dragged marker there cancels the gesture.
    if (!dragging) markers.forEach((marker) => marker.remove());
    if (active()) {
      if (!dragging) {
        if (a) markers[0].setLngLat(a).addTo(map);
        if (b) markers[1].setLngLat(b).addTo(map);
      }
      if (home && a) features.push(line([home, a], "guide"));
      if (turnaround && b) features.push(line([turnaround, b], "guide"));
      if (a && b && roundTrip()) {
        const distance = separation(a, b);
        const midpoint: Location = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
        const radius = distance * Number(avoidance.value);
        features.push(line([a, b], "guide"));
        centreMarker.setLngLat(circlePoint(midpoint, radius, 0)).addTo(map);
        features.push(line(circleCoordinates(midpoint, radius), "centre"));
      }
      element("run-avoidance-value").textContent = `${Math.round(Number(avoidance.value) * 100)}%`;
    }
    const candidate = response?.candidates[selected];
    if (candidate) {
      features.push(line(runCoordinates(candidate), "route"));
      element("run-direction-label").textContent =
        `${candidateName(candidate)} · arrows show travel direction`;
    }
    displayFeatures = features;
    (map.getSource("run-geometry") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features,
    });
    for (const [id, location, snap] of [
      ["run-home-value", home, homeSnap],
      ["run-turnaround-value", turnaround, turnaroundSnap],
    ] as const) {
      element(id).textContent = location
        ? snap
          ? `Selected · ${snap.offset_m.toFixed(1)} m snap`
          : "Selected"
        : "Choose on map";
    }
    const generateButton = element<HTMLButtonElement>("run-generate");
    generateButton.disabled = generating || !home || !turnaround;
    generateButton.textContent = generating ? "Generating run…" : "Generate run";
    generateButton.setAttribute("aria-busy", String(generating));
    element<HTMLButtonElement>("run-fit").disabled = !candidate;
    element<HTMLButtonElement>("run-elevation").disabled = !candidate;
    element("run-results").hidden = !candidate || !active();
  }
  function pick(value?: "home" | "turnaround"): void {
    if (value) document.dispatchEvent(new Event("plannerpick"));
    const wasPicking = Boolean(choosing);
    if (value && !wasPicking) pickStatus = status.textContent ?? "";
    choosing = value;
    element("run-home").textContent =
      value === "home" ? "Cancel" : options.state.home ? "Change" : "Set";
    element("run-turnaround").textContent =
      value === "turnaround" ? "Cancel" : turnaround ? "Change" : "Set";
    element("run-home").setAttribute("aria-pressed", String(value === "home"));
    element("run-turnaround").setAttribute("aria-pressed", String(value === "turnaround"));
    element("run-home").setAttribute(
      "aria-label",
      value === "home"
        ? "Cancel start selection"
        : options.state.home
          ? "Change start"
          : "Set start on map",
    );
    element("run-turnaround").setAttribute(
      "aria-label",
      value === "turnaround"
        ? `Cancel ${endpointLabel()} selection`
        : turnaround
          ? `Change ${endpointLabel()}`
          : `Set ${endpointLabel()} on map`,
    );
    map.getCanvas().style.cursor = value ? "crosshair" : "";
    if (value)
      status.textContent =
        value === "home"
          ? "Click the map to set your start."
          : `Click the map to set your ${endpointLabel()}.`;
    else if (wasPicking) status.textContent = pickStatus;
  }
  function invalidate(): void {
    profile.close();
    observedInputs = inputsKey();
    abort?.abort();
    ++sequence;
    generating = false;
    element("run-cancel").hidden = true;
    status.textContent = response?.candidates.length
      ? "Displayed run uses previous settings. Generate again to update."
      : "";
    draw();
  }
  async function snapEndpoints(): Promise<void> {
    snappingAbort?.abort();
    snappingAbort = new AbortController();
    const signal = snappingAbort.signal;
    const current = ++snapSequence;
    homeSnap = turnaroundSnap = null;
    const home = options.state.home;
    const policy = options.state.policy;
    const locations = [home, turnaround];
    try {
      const snaps = await Promise.all(
        locations.map(async (location) => {
          if (!location) return null;
          return snapEndpoint(options.datasetId, location, policy, signal);
        }),
      );
      if (current !== snapSequence) return;
      [homeSnap, turnaroundSnap] = snaps;
      if (home && !homeSnap)
        status.textContent = "Start has no eligible path within the snap radius.";
      else if (turnaround && !turnaroundSnap)
        status.textContent = `${roundTrip() ? "Turnaround" : "Destination"} has no eligible path within the snap radius.`;
      draw();
    } catch (error) {
      if (current === snapSequence && (error as Error).name !== "AbortError")
        status.textContent = (error as Error).message;
    }
  }
  function selectOrientation(): void {
    if (!roundTrip() || response?.cost_model.shape_settings.avoidance_ratio === 0) {
      selected = response?.candidates.length ? 0 : -1;
      return;
    }
    selected =
      response?.candidates.findIndex(
        (candidate) => candidate.outbound_left === (orientation.value === "clockwise"),
      ) ?? -1;
  }
  function candidates(): void {
    profile.close();
    const container = element("run-candidates");
    container.replaceChildren();
    const candidate = response?.candidates[selected];
    if (!candidate) return;
    const summary = document.createElement("div");
    summary.className = "run-summary";
    summary.textContent = `${(candidate.distance_m / 1000).toFixed(2)} km`;
    container.append(summary);
    element("run-detail-content").textContent =
      `${candidateDetails(candidate)} ${response?.coverage_note ?? ""}`;
  }
  async function generate(): Promise<void> {
    const home = options.state.home;
    if (!home || !turnaround) return;
    abort?.abort();
    abort = new AbortController();
    const current = ++sequence;
    const requestedInputs = inputsKey();
    const requestedMode = routeMode;
    generating = true;
    draw();
    status.textContent = response?.candidates.length
      ? "Generating… displayed run uses previous settings."
      : "Generating run…";
    element("run-cancel").hidden = false;
    try {
      const next = await requestRun(
        {
          dataset_id: options.datasetId,
          mode: requestedMode,
          home: { longitude: home[0], latitude: home[1] },
          turnaround: { longitude: turnaround[0], latitude: turnaround[1] },
          ...options.state.policy,
          settings: {
            avoidance_ratio: roundTrip() ? Number(avoidance.value) : 0,
            centre_enabled: true,
          },
          preferences: { hills: hillPreset().level },
        },
        abort.signal,
      );
      if (current !== sequence) return;
      if (next.mode !== requestedMode) throw new Error("Route mode changed; generate again.");
      if (next.dataset_id !== options.datasetId)
        throw new Error("Dataset changed; reload the map.");
      response = next;
      responseInputs = requestedInputs;
      selectOrientation();
      homeSnap = next.home;
      turnaroundSnap = next.turnaround;
      const message =
        next.status === "complete"
          ? selected >= 0
            ? "Run ready."
            : "No run found for this orientation. Try the other direction."
          : next.status === "deadline"
            ? "Search deadline reached. Any completed runs are shown."
            : next.status === "area_limited"
              ? "The search reached its area limit. Try closer endpoints."
              : next.status === "no_eligible_snap"
                ? `${!next.home ? "Start" : roundTrip() ? "Turnaround" : "Destination"} could not snap to an eligible path.`
                : roundTrip()
                  ? "No complete return run connects these points under the current path filters."
                  : "No route reaches the destination under the current path filters.";
      status.textContent = message;
      candidates();
      draw();
    } catch (error) {
      if (current === sequence && (error as Error).name !== "AbortError")
        status.textContent = `Run request failed: ${(error as Error).message}`;
    } finally {
      if (current === sequence) {
        generating = false;
        element("run-cancel").hidden = true;
        draw();
      }
    }
  }
  function beginDrag(): void {
    profile.close();
    dragging = true;
    pick();
    snappingAbort?.abort();
    ++snapSequence;
    abort?.abort();
    ++sequence;
    generating = false;
    element("run-cancel").hidden = true;
    element<HTMLButtonElement>("run-generate").disabled = true;
    status.textContent = "Move the marker, then generate again to update the run.";
  }
  function endDrag(): void {
    window.setTimeout(() => {
      dragging = false;
    }, 0);
  }
  markers.forEach((marker, index) => {
    marker.getElement().title =
      index === 0 ? "Drag to move start" : `Drag to move ${endpointLabel()}`;
    marker.on("dragstart", beginDrag);
    marker.on("dragend", () => {
      const point = marker.getLngLat();
      const location: Location = [point.lng, point.lat];
      homeSnap = turnaroundSnap = null;
      if (index === 0) options.setHome(location);
      else {
        turnaround = location;
        invalidate();
        void snapEndpoints();
      }
      endDrag();
    });
  });
  function resizeCircle(location: Location): void {
    const a = markers[0].getLngLat();
    const b = markers[1].getLngLat();
    const distance = separation([a.lng, a.lat], [b.lng, b.lat]);
    if (distance < 0.01) return;
    const midpoint: Location = [(a.lng + b.lng) / 2, (a.lat + b.lat) / 2];
    avoidance.value = String(separation(midpoint, location) / distance);
    const radius = distance * Number(avoidance.value);
    element("run-avoidance-value").textContent = `${Math.round(Number(avoidance.value) * 100)}%`;
    (map.getSource("run-geometry") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: [
        ...displayFeatures.filter((feature) => feature.properties?.kind !== "centre"),
        line(circleCoordinates(midpoint, radius), "centre"),
      ],
    });
  }
  centreMarker.on("dragstart", beginDrag);
  centreMarker.on("drag", () => {
    const point = centreMarker.getLngLat();
    resizeCircle([point.lng, point.lat]);
  });
  centreMarker.on("dragend", () => {
    invalidate();
    endDrag();
  });
  let finishCircleDrag: (() => void) | undefined;
  function dragCircle(event: MapMouseEvent | MapTouchEvent): void {
    if (!active() || !roundTrip()) return;
    event.preventDefault();
    const panEnabled = map.dragPan.isEnabled();
    beginDrag();
    map.dragPan.disable();
    const move = (next: MapMouseEvent | MapTouchEvent): void => {
      resizeCircle([next.lngLat.lng, next.lngLat.lat]);
      const a = markers[0].getLngLat();
      const b = markers[1].getLngLat();
      const midpoint: Location = [(a.lng + b.lng) / 2, (a.lat + b.lat) / 2];
      const radius = separation([a.lng, a.lat], [b.lng, b.lat]) * Number(avoidance.value);
      centreMarker.setLngLat(circlePoint(midpoint, radius, 0));
    };
    const finish = (): void => {
      finishCircleDrag = undefined;
      map.off("mousemove", move);
      map.off("touchmove", move);
      map.off("mouseup", finish);
      map.off("touchend", finish);
      map.off("touchcancel", finish);
      if (panEnabled) map.dragPan.enable();
      invalidate();
      endDrag();
    };
    finishCircleDrag = finish;
    map.on("mousemove", move);
    map.on("touchmove", move);
    map.on("mouseup", finish);
    map.on("touchend", finish);
    map.on("touchcancel", finish);
  }
  map.on("mousedown", "run-centre-hit", dragCircle);
  map.on("touchstart", "run-centre-hit", dragCircle);
  map.on("mouseenter", "run-centre-hit", () => {
    if (active()) map.getCanvas().style.cursor = "ew-resize";
  });
  map.on("mouseleave", "run-centre-hit", () => {
    map.getCanvas().style.cursor = choosing ? "crosshair" : "";
  });
  function modeChanged(): void {
    profile.close();
    finishCircleDrag?.();
    dragging = false;
    const interrupted = generating;
    abort?.abort();
    ++sequence;
    generating = false;
    element("run-cancel").hidden = true;
    snappingAbort?.abort();
    ++snapSequence;
    pick();
    if (interrupted) status.textContent = "Request cancelled. Generate again to update.";
    const nextMode = options.state.plannerMode;
    if (nextMode !== "off" && nextMode !== routeMode) {
      routeMode = nextMode;
      response = undefined;
      responseInputs = undefined;
      selected = -1;
      homeSnap = turnaroundSnap = null;
      invalidate();
    } else if (active() && observedInputs !== inputsKey()) invalidate();
    element("run-endpoint-label").textContent = roundTrip() ? "Turnaround" : "Destination";
    element("run-shape-controls").hidden = !roundTrip();
    markers[1].getElement().title = "Drag to move " + endpointLabel();
    pick(
      active()
        ? !options.state.home
          ? "home"
          : roundTrip()
            ? regionSelectionReady
              ? "turnaround"
              : undefined
            : !turnaround
              ? "turnaround"
              : undefined
        : undefined,
    );
    candidates();
    draw();
    if (active()) void snapEndpoints();
  }
  function sharedInputsChanged(): void {
    snappingAbort?.abort();
    ++snapSequence;
    homeSnap = turnaroundSnap = null;
    invalidate();
    if (active()) void snapEndpoints();
  }
  element("run-home").addEventListener("click", () =>
    pick(choosing === "home" ? undefined : "home"),
  );
  element("run-turnaround").addEventListener("click", () =>
    pick(choosing === "turnaround" ? undefined : "turnaround"),
  );
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && choosing) pick();
  });
  element("run-clear").addEventListener("click", () => {
    turnaround = undefined;
    response = undefined;
    homeSnap = turnaroundSnap = null;
    snappingAbort?.abort();
    ++snapSequence;
    pick();
    options.setHome(undefined);
    pick("home");
    candidates();
  });
  document.addEventListener("distancepick", () => pick());
  orientation.addEventListener("change", () => {
    selectOrientation();
    candidates();
    draw();
    if (response)
      status.textContent = generating
        ? "Generating… displayed run uses previous settings."
        : responseInputs !== inputsKey()
          ? "Displayed run uses previous settings. Generate again to update."
          : selected >= 0
            ? "Run ready."
            : "No run found for this orientation. Try the other direction.";
  });
  avoidance.addEventListener("input", invalidate);
  hills.addEventListener("input", () => {
    updateHillPreset();
    invalidate();
  });
  element("run-generate").addEventListener("click", () => {
    void generate();
  });
  element("run-cancel").addEventListener("click", () => {
    invalidate();
    status.textContent = "Cancelled. The server stops work at its deadline.";
  });
  element("run-fit").addEventListener("click", () => {
    const candidate = response?.candidates[selected];
    if (!candidate) return;
    const bounds = new LngLatBounds();
    runCoordinates(candidate).forEach((point) => bounds.extend(point));
    map.fitBounds(bounds, { padding: mapPadding(), maxZoom: 16 });
  });
  element("run-elevation").addEventListener("click", () => {
    const candidate = response?.candidates[selected];
    if (candidate) void profile.toggle(candidate);
  });
  return {
    modeChanged,
    homeChanged: sharedInputsChanged,
    policyChanged: sharedInputsChanged,
    picking: () => Boolean(choosing),
    click: (location, point) => {
      if (dragging) return true;
      if (!choosing) {
        const candidate = response?.candidates[selected];
        if (candidate && map.queryRenderedFeatures(point, { layers: ["run-route"] }).length) {
          void profile.open(candidate);
          return true;
        }
        return false;
      }
      const endpoint = choosing;
      pick();
      if (endpoint === "home") {
        options.setHome(location);
        if (!roundTrip()) pick("turnaround");
      } else {
        turnaround = location;
        invalidate();
        void snapEndpoints();
      }
      return true;
    },
  };
}

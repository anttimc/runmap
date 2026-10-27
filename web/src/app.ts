/** Coordinate shared start/policy inputs and dispatch map interaction to feature controllers. */
import type { Map } from "maplibre-gl";
import { loadPathConfig } from "./api";
import type { ColorStop, Location, PathConfig, PathPolicy } from "./api-types";
import { element } from "./dom";
import { startPaths, type PathControls } from "./paths";
import { startRunPlanner, type RunControls } from "./runs";
import { createSharedState } from "./state";

function readPathPolicy(): PathPolicy {
  const surface = element<HTMLSelectElement>("path-surface").value;
  return {
    surface: surface === "surfaced" ? "surfaced" : "all",
    avoid_stairs: element<HTMLInputElement>("path-avoid-stairs").checked,
    radius_m: Number(element<HTMLInputElement>("outing-radius").value),
  };
}

async function readyPathConfig(): Promise<PathConfig | undefined> {
  const status = element("path-status");
  try {
    const config = await loadPathConfig();
    if (config.status === "ready") return config;
    status.textContent =
      config.status === "failed"
        ? "Path analysis failed to load. Check prepared artifacts."
        : "Path analysis unavailable — prepare local measurements.";
    status.dataset.state = config.status;
  } catch {
    status.textContent = "Path configuration request failed. Terrain remains available.";
    status.dataset.state = "failed";
  }
  return undefined;
}

/** Coordinate shared inputs and dispatch map interaction to the active tool. */
export async function startMapTools(map: Map, stops: ColorStop[]): Promise<void> {
  const config = await readyPathConfig();
  if (!config) return;
  element<HTMLInputElement>("outing-radius").value = String(config.snap_radius_m);
  for (const id of ["path-surface", "path-avoid-stairs", "outing-radius"]) {
    element<HTMLInputElement | HTMLSelectElement>(id).disabled = false;
  }
  const state = createSharedState(readPathPolicy());
  let paths: PathControls;
  let planner: RunControls | undefined;

  function setHome(location: Location | undefined): void {
    state.home = location;
    paths.homeChanged();
    planner?.homeChanged();
  }

  paths = startPaths(map, stops, config, {
    state,
    setHome,
    plannerPicking: () => planner?.picking() ?? false,
  });
  planner = startRunPlanner(map, {
    datasetId: config.dataset_id,
    runPlanner: config.run_planner,
    state,
    setHome,
  });

  const planMode = element<HTMLSelectElement>("plan-mode");
  planMode.disabled = false;
  planMode.addEventListener("change", () => {
    state.plannerMode =
      planMode.value === "one_way" || planMode.value === "round_trip" ? planMode.value : "off";
    const enabled = state.plannerMode !== "off";
    element("run-controls").hidden = !enabled;
    element("run-footer").hidden = !enabled;
    element("run-clear").hidden = !enabled;
    document.querySelector<HTMLElement>(".controls")!.dataset.planning = String(enabled);
    if (enabled) paths.modeChanged();
    planner?.modeChanged();
  });
  document.addEventListener("plannerpick", () => paths.modeChanged());

  for (const id of ["path-surface", "path-avoid-stairs", "outing-radius"]) {
    element<HTMLInputElement | HTMLSelectElement>(id).addEventListener("change", (event) => {
      if (!(event.target as HTMLInputElement | HTMLSelectElement).reportValidity()) return;
      state.policy = readPathPolicy();
      paths.policyChanged();
      planner?.policyChanged();
    });
  }
  map.on("click", (event) => {
    if (planner?.click([event.lngLat.lng, event.lngLat.lat])) return;
    paths.click(event);
  });
}

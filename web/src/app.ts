import type { Map } from "maplibre-gl";
import { loadPathConfig } from "./api";
import type { ColorStop, Location, PathConfig, PathPolicy } from "./api-types";
import { element } from "./dom";
import { startPaths, type PathControls } from "./paths";
import { startRunPlanner, type RunControls } from "./runs";
import { createSharedState, type ToolMode } from "./state";

function readPathPolicy(): PathPolicy {
  const surface = element<HTMLSelectElement>("path-surface").value;
  return {
    surface: surface === "surfaced" ? "surfaced" : "all",
    avoid_stairs: element<HTMLInputElement>("path-avoid-stairs").checked,
    radius_m: Number(element<HTMLInputElement>("outing-radius").value),
  };
}

function showMode(mode: ToolMode): void {
  const planning = mode === "plan";
  element("run-controls").hidden = !planning;
  element("explore-controls").hidden = planning;
  element("run-footer").hidden = !planning;
  for (const [id, selected] of [
    ["tool-explore", !planning],
    ["tool-run", planning],
  ] as const) {
    const tab = element<HTMLButtonElement>(id);
    tab.disabled = false;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
}

function bindModeTabs(changeMode: (mode: ToolMode) => void): void {
  const tabs = [element<HTMLButtonElement>("tool-explore"), element<HTMLButtonElement>("tool-run")];
  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => changeMode(index === 0 ? "explore" : "plan"));
    tab.addEventListener("keydown", (event) => {
      const next =
        event.key === "ArrowRight"
          ? (index + 1) % tabs.length
          : event.key === "ArrowLeft"
            ? (index + tabs.length - 1) % tabs.length
            : event.key === "Home"
              ? 0
              : event.key === "End"
                ? tabs.length - 1
                : undefined;
      if (next === undefined || tabs[next].disabled) return;
      event.preventDefault();
      tabs[next].focus();
      tabs[next].click();
    });
  }
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

  bindModeTabs((mode) => {
    if (mode === state.mode) return;
    state.mode = mode;
    showMode(mode);
    paths.modeChanged();
    planner?.modeChanged();
  });
  showMode(state.mode);

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

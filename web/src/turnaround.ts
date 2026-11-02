/** Refresh advisory turnaround shading without requesting individual routes. */
import type { GeoJSONSource, Map } from "maplibre-gl";
import type { SnapshotResponse } from "./api-types";
import type { SharedState } from "./state";
import { ApiError, loadTurnaroundRegion, prepareSnapshot } from "./api.ts";
import { element } from "./dom.ts";

interface Options {
  datasetId: string;
  supported: boolean;
  state: Readonly<SharedState>;
  width: () => number;
  hills: () => boolean;
  selectionChanged: (ready: boolean) => void;
}

export function startTurnaroundRegion(map: Map, options: Options): () => void {
  const target = element<HTMLInputElement>("run-target-distance");
  const enabled = element<HTMLInputElement>("run-target-enabled");
  const manual = element<HTMLInputElement>("run-target-manual");
  const status = element("run-target-status");
  const method = element("run-turnaround-method");
  const controls = element("run-target-controls");
  const endpoint = element("run-turnaround-row");
  function selection(ready: boolean): void {
    endpoint.hidden = !ready;
    options.selectionChanged(ready);
  }
  enabled.disabled = !options.supported;
  target.disabled = !options.supported;
  const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
  map.addSource("turnaround-region", { type: "geojson", data: empty });
  const before =
    map.getStyle().layers?.find((layer) => layer.id === "context-road-casing")?.id ??
    map.getStyle().layers?.find((layer) => layer.type === "symbol")?.id;
  map.addLayer(
    {
      id: "turnaround-region-fill",
      type: "fill",
      source: "turnaround-region",
      paint: { "fill-color": "#087f8c", "fill-opacity": ["case", ["get", "core"], 0.34, 0.1] },
    },
    before,
  );
  map.addLayer(
    {
      id: "turnaround-region-outline",
      type: "line",
      source: "turnaround-region",
      filter: ["==", ["get", "core"], true],
      paint: { "line-color": "#087f8c", "line-width": 1.2 },
    },
    before,
  );
  let controller: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let sequence = 0;
  let observed = "";
  let snapshot: { key: string; value: SnapshotResponse } | undefined;
  const setData = (data: GeoJSON.FeatureCollection): void => {
    (map.getSource("turnaround-region") as GeoJSONSource).setData(data);
  };
  async function load(current: number): Promise<void> {
    const home = options.state.home;
    if (!home) return;
    const targetM = Number(target.value) * 1000;
    const width = options.width();
    const budget = Math.ceil((targetM * 1.15) / 1000);
    const key = JSON.stringify([home, options.state.policy, budget]);
    controller = new AbortController();
    const signal = controller.signal;
    status.textContent = "Estimating turnaround region…";
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        if (!snapshot || snapshot.key !== key || snapshot.value.expires_at * 1000 <= Date.now()) {
          const value = await prepareSnapshot(
            {
              dataset_id: options.datasetId,
              longitude: home[0],
              latitude: home[1],
              ...options.state.policy,
              budget_km: budget,
            },
            signal,
          );
          if (current !== sequence) return;
          if (value.dataset_id !== options.datasetId)
            throw new Error("Dataset changed; reload the map.");
          if (!value.snapshot_id)
            throw new Error("Start has no eligible path within the snap radius.");
          snapshot = { key, value };
        }
        try {
          const region = await loadTurnaroundRegion(
            options.datasetId,
            snapshot.value.snapshot_id,
            targetM,
            width,
            signal,
          );
          if (current !== sequence) return;
          if (
            region.dataset_id !== options.datasetId ||
            region.snapshot_id !== snapshot.value.snapshot_id ||
            region.target_m !== targetM ||
            region.width !== width
          )
            throw new Error("Turnaround inputs changed; try again.");
          setData(region);
          status.textContent = region.features.length
            ? ""
            : "No estimated region near this target. Try another distance or width.";
          selection(true);
          return;
        } catch (error) {
          if (error instanceof ApiError && error.status === 410 && attempt === 0)
            snapshot = undefined;
          else throw error;
        }
      }
    } catch (error) {
      if (current === sequence && (error as Error).name !== "AbortError")
        status.textContent = `Region unavailable: ${(error as Error).message}`;
    }
  }
  function refresh(): void {
    const key = JSON.stringify([
      options.state.plannerMode,
      options.state.home,
      options.state.policy,
      enabled.checked,
      manual.checked,
      target.value,
      options.width(),
      options.hills(),
    ]);
    if (key === observed) return;
    observed = key;
    controller?.abort();
    clearTimeout(timer);
    const current = ++sequence;
    setData(empty);
    status.textContent = "";
    const roundTrip = options.state.plannerMode === "round_trip";
    method.hidden = !roundTrip || !options.state.home;
    controls.hidden = method.hidden || !enabled.checked;
    if (!roundTrip) {
      endpoint.hidden = options.state.plannerMode !== "one_way";
      return;
    }
    if (!options.state.home) {
      selection(false);
      return;
    }
    if (manual.checked) {
      selection(true);
      return;
    }
    selection(false);
    if (!enabled.checked || !options.supported) return;
    if (!target.value) return;
    if (!target.checkValidity()) {
      status.textContent = "Choose a target between 0.5 and 40 km.";
      return;
    }
    status.textContent = "Estimating turnaround region…";
    timer = setTimeout(() => {
      void load(current);
    }, 300);
  }
  target.addEventListener("input", refresh);
  enabled.addEventListener("change", refresh);
  enabled.addEventListener("change", () => {
    if (enabled.checked) target.focus();
  });
  manual.addEventListener("change", refresh);
  return refresh;
}

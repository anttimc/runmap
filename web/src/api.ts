import type {
  Inspection,
  Location,
  MapConfig,
  PathConfig,
  PathPolicy,
  PreferenceLevel,
  RunResponse,
  SnapshotResponse,
  Snap,
  ShellMethod,
  ShellResponse,
} from "./api-types";

interface UnavailablePathConfig {
  status: "unavailable" | "failed";
  message: string;
}

interface SnapshotRequest extends PathPolicy {
  dataset_id: string;
  longitude: number;
  latitude: number;
  budget_km: number;
}

interface RunRequest extends PathPolicy {
  dataset_id: string;
  home: { longitude: number; latitude: number };
  turnaround: { longitude: number; latitude: number };
  settings: { avoidance_ratio: number; centre_enabled: boolean };
  preferences: { hills: PreferenceLevel };
}

async function errorMessage(response: Response): Promise<string> {
  if (response.status === 409) return "Dataset changed — reload the map.";
  const fallback = `Request failed (${response.status}).`;
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
    ) {
      return body.detail;
    }
  } catch {
    // A proxy or server failure need not return JSON.
  }
  return fallback;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function requestJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
  return (await response.json()) as T;
}

function postJson<T>(
  url: string,
  body: SnapshotRequest | RunRequest,
  signal: AbortSignal,
): Promise<T> {
  return requestJson<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify(body),
  });
}

export function loadMapConfig(): Promise<MapConfig> {
  return requestJson("/api/config");
}

export function loadPathConfig(): Promise<PathConfig | UnavailablePathConfig> {
  return requestJson("/api/paths/config");
}

export function loadInspection(
  segmentId: string,
  parameters: URLSearchParams,
  signal: AbortSignal,
): Promise<Inspection> {
  return requestJson(`/api/paths/segments/${encodeURIComponent(segmentId)}?${parameters}`, {
    signal,
  });
}

export function prepareSnapshot(
  body: SnapshotRequest,
  signal: AbortSignal,
): Promise<SnapshotResponse> {
  return postJson("/api/paths/snapshots", body, signal);
}

export function loadDistanceShells(
  datasetId: string,
  snapshotId: string,
  method: ShellMethod,
  signal: AbortSignal,
): Promise<ShellResponse> {
  const parameters = new URLSearchParams({ dataset_id: datasetId, method });
  return requestJson(
    `/api/paths/snapshots/${encodeURIComponent(snapshotId)}/shells?${parameters}`,
    { signal },
  );
}

export async function snapEndpoint(
  datasetId: string,
  location: Location,
  policy: Readonly<PathPolicy>,
  signal: AbortSignal,
): Promise<Snap | null> {
  const parameters = new URLSearchParams({
    dataset_id: datasetId,
    longitude: String(location[0]),
    latitude: String(location[1]),
    surface: policy.surface,
    avoid_stairs: String(policy.avoid_stairs),
    radius_m: String(policy.radius_m),
  });
  const result = await requestJson<{ snap: Snap | null }>(`/api/paths/snap?${parameters}`, {
    signal,
  });
  return result.snap;
}

export function requestRun(body: RunRequest, signal: AbortSignal): Promise<RunResponse> {
  return postJson("/api/paths/run", body, signal);
}

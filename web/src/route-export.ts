/** Share elevation preparation between downloaded and connected route exports. */
import { loadRouteProfile } from "./api.ts";
import type { Candidate, RouteProfile } from "./api-types";

export async function loadExportProfile(
  datasetId: string,
  candidate: Candidate,
): Promise<RouteProfile> {
  const profile = await loadRouteProfile(
    datasetId,
    candidate.legs.map((leg) => leg.traversals),
    new AbortController().signal,
  );
  if (profile.dataset_id !== datasetId) throw new Error("Dataset changed — reload the map.");
  return profile;
}

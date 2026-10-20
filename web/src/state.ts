import type { Location, PathPolicy } from "./api-types";

export type ToolMode = "explore" | "plan";

/** Shared inputs owned by the application coordinator. */
export interface SharedState {
  mode: ToolMode;
  home: Location | undefined;
  policy: Readonly<PathPolicy>;
}

export function createSharedState(policy: PathPolicy): SharedState {
  return { mode: "explore", home: undefined, policy: { ...policy } };
}

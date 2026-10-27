/** Describe shared planner activation, start location, and routing policy state. */
import type { Location, PathPolicy, RunMode } from "./api-types";

export type PlannerMode = "off" | RunMode;

/** Shared inputs owned by the application coordinator. */
export interface SharedState {
  /** Planner activation only; map layers remain independent. */
  plannerMode: PlannerMode;
  home: Location | undefined;
  policy: Readonly<PathPolicy>;
}

export function createSharedState(policy: PathPolicy): SharedState {
  return { plannerMode: "off", home: undefined, policy: { ...policy } };
}

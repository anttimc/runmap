/** Verify route presentation for one-way and round-trip candidates. */
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  candidateDetails,
  candidateName,
  environmentDetails,
  runCoordinates,
} from "../src/run-results.ts";

const outbound = {
  coordinates: [
    [24.8, 60.2],
    [24.81, 60.21],
  ],
  distance_m: 1200,
};
const returning = {
  coordinates: [
    [24.81, 60.21],
    [24.8, 60.2],
  ],
  distance_m: 1200,
};

test("one-way rendering ends at the destination and omits loop diagnostics", () => {
  const candidate = { mode: "one_way", legs: [outbound], distance_m: 1200 };
  assert.deepEqual(runCoordinates(candidate), outbound.coordinates);
  assert.equal(candidateName(candidate), "One way");
  assert.equal(candidateDetails(candidate), "Start to destination 1.20 km.");
});

test("round-trip rendering joins both legs and retains out-and-back details", () => {
  const candidate = {
    mode: "round_trip",
    legs: [outbound, returning],
    distance_m: 2400,
    repeated_distance_m: 1200,
    closest_centre_m: 0,
    encloses_centre: null,
    outbound_left: true,
  };
  assert.deepEqual(runCoordinates(candidate), [...outbound.coordinates, returning.coordinates[1]]);
  assert.equal(candidateName(candidate), "Out and back");
  assert.match(candidateDetails(candidate), /Out 1.20 km · back 1.20 km/);
  assert.match(candidateDetails(candidate), /1200 m repeated/);
  const detour = {
    ...returning,
    coordinates: [returning.coordinates[0], [24.82, 60.19], returning.coordinates[1]],
  };
  assert.equal(candidateName({ ...candidate, legs: [outbound, detour] }), "Clockwise");
  assert.equal(
    candidateName({ ...candidate, legs: [outbound, detour], outbound_left: false }),
    "Counter-clockwise",
  );
});

test("environment details use route settings and omit Off fields and zero missing coverage", () => {
  const candidate = {
    legs: [
      {
        cost_breakdown: { environment_greenery: 25 },
        observations: { environment_unknown_greenery_m: 0 },
      },
    ],
  };
  assert.equal(environmentDetails(candidate), "");
  assert.equal(environmentDetails(candidate, { environment_greenery: "off" }), "");
  const text = environmentDetails(candidate, { environment_greenery: "high" });
  assert.match(text, /Greenery · High   \+0.03 eq km/);
  assert.doesNotMatch(text, /unknown|lacks|Traffic/);
  candidate.legs[0].observations.environment_unknown_greenery_m = 12;
  assert.match(
    environmentDetails(candidate, { environment_greenery: "high" }),
    /12 m of the route lacks mapped context/,
  );
});

test("signal details report repeated and uncertain passages without claiming complete coverage", () => {
  const candidate = {
    legs: [
      { observations: { signal_passages: 2, unresolved_signal_passages: 1 } },
      { observations: { signal_passages: 2, unresolved_signal_passages: 0 } },
    ],
  };
  const text = environmentDetails(candidate, { signals: "high" });
  assert.match(text, /Traffic lights · High: 4 mapped passages, 1 uncertain passages/);
  assert.match(text, /Mapping may omit lights/);
  assert.equal(environmentDetails(candidate, { signals: "off" }), "");
});

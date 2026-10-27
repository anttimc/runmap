import assert from "node:assert/strict";
import { test } from "node:test";
import { candidateDetails, candidateName, runCoordinates } from "../src/run-results.ts";

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

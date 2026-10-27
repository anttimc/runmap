import type { Candidate, Location } from "./api-types";

export function runCoordinates(candidate: Candidate): Location[] {
  return candidate.legs.flatMap((leg, index) =>
    index ? leg.coordinates.slice(1) : leg.coordinates,
  );
}

export function candidateName(candidate: Candidate): string {
  if (candidate.mode === "one_way") return "One way";
  const [outbound, returning] = candidate.legs;
  const retraced =
    outbound.coordinates.length === returning.coordinates.length &&
    outbound.coordinates.every((point, index) => {
      const reverse = returning.coordinates[returning.coordinates.length - 1 - index];
      return Math.abs(point[0] - reverse[0]) < 1e-9 && Math.abs(point[1] - reverse[1]) < 1e-9;
    });
  return retraced ? "Out and back" : candidate.outbound_left ? "Clockwise" : "Counter-clockwise";
}

export function candidateDetails(candidate: Candidate): string {
  if (candidate.mode === "one_way")
    return `Start to destination ${(candidate.distance_m / 1000).toFixed(2)} km.`;
  const shape =
    candidate.encloses_centre === true
      ? "Encloses centre"
      : candidate.encloses_centre === false
        ? "Does not enclose centre"
        : "Retracing or crossings: enclosure unclear";
  return `Out ${(candidate.legs[0].distance_m / 1000).toFixed(2)} km · back ${(candidate.legs[1].distance_m / 1000).toFixed(2)} km. ${Math.round(candidate.repeated_distance_m)} m repeated. ${shape} · closest approach ${Math.round(candidate.closest_centre_m ?? 0)} m.`;
}

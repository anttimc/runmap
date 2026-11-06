/** Present route geometry, names, and details for one-way and round-trip results. */
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

/** Describe enabled preferences using the settings returned with this route. */
export function environmentDetails(
  candidate: Candidate,
  settings: Record<string, string> = {},
): string {
  const labels = {
    greenery: "Greenery",
    water: "Water",
    traffic: "Traffic avoidance",
    wetland: "Wetland avoidance",
  };
  const details: string[] = [];
  for (const [field, label] of Object.entries(labels)) {
    const level = settings[`environment_${field}`];
    if (!level || level === "off") continue;
    const penalty = candidate.legs.reduce(
      (sum, leg) => sum + (leg.cost_breakdown?.[`environment_${field}`] ?? 0),
      0,
    );
    let detail = `${label} · ${level === "high" ? "High" : "Low"}   +${(penalty / 1000).toFixed(2)} eq km`;
    const coverage = candidate.legs.map(
      (leg) => leg.observations?.[`environment_unknown_${field}_m`],
    );
    if (coverage.some((value) => value == null)) {
      detail += "; map coverage unavailable";
    } else {
      const missing = coverage.reduce<number>((sum, value) => sum + (value ?? 0), 0);
      if (missing >= 0.5) detail += `; ${Math.round(missing)} m of the route lacks mapped context`;
    }
    details.push(detail);
  }
  return details.length
    ? "Environment routing penalties\n" + details.join("\n") +
        "\nEquivalent kilometres express preference costs, not extra running distance."
    : "";
}

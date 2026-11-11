/** Export displayed route geometry as a portable GPX track. */
import type { Candidate, Location, RouteProfile } from "./api-types";
import { candidateName, runCoordinates } from "./run-results.ts";

function trackName(candidate: Candidate): string {
  return `Runmap · ${candidateName(candidate)} · ${(candidate.distance_m / 1000).toFixed(2)} km`;
}

function profilePoints(profile: RouteProfile): { location: Location; elevation: number | null }[] {
  return profile.intervals.flatMap((interval, index) => [
    ...(index === 0
      ? [{ location: interval.coordinates[0], elevation: interval.start_elevation_m }]
      : []),
    {
      location: interval.coordinates[interval.coordinates.length - 1],
      elevation: interval.end_elevation_m,
    },
  ]);
}

export function routeGpx(candidate: Candidate, profile?: RouteProfile): string {
  const samples = profile?.intervals.length
    ? profilePoints(profile)
    : runCoordinates(candidate).map((location) => ({ location, elevation: null }));
  const points = samples.map(({ location: [longitude, latitude], elevation }) =>
    elevation !== null && Number.isFinite(elevation)
      ? `      <trkpt lat="${latitude}" lon="${longitude}"><ele>${elevation}</ele></trkpt>`
      : `      <trkpt lat="${latitude}" lon="${longitude}" />`,
  );
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<gpx version="1.1" creator="Runmap" xmlns="http://www.topografix.com/GPX/1/1">',
    "  <trk>",
    `    <name>${trackName(candidate)}</name>`,
    "    <type>running</type>",
    "    <trkseg>",
    ...points,
    "    </trkseg>",
    "  </trk>",
    "</gpx>",
    "",
  ].join("\n");
}

export function downloadRouteGpx(candidate: Candidate, profile?: RouteProfile): void {
  const blob = new Blob([routeGpx(candidate, profile)], {
    type: "application/gpx+xml;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `runmap-${candidateName(candidate).toLowerCase().replaceAll(" ", "-")}-${(candidate.distance_m / 1000).toFixed(2)}km.gpx`;
  document.body.append(link);
  try {
    link.click();
  } finally {
    link.remove();
    // Give the browser time to start reading the download before releasing it.
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
}

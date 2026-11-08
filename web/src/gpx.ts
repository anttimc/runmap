/** Export displayed route geometry as a portable GPX track. */
import type { Candidate } from "./api-types";
import { candidateName, runCoordinates } from "./run-results.ts";

function trackName(candidate: Candidate): string {
  return `Runmap · ${candidateName(candidate)} · ${(candidate.distance_m / 1000).toFixed(2)} km`;
}

export function routeGpx(candidate: Candidate): string {
  const points = runCoordinates(candidate).map(
    ([longitude, latitude]) => `      <trkpt lat="${latitude}" lon="${longitude}" />`,
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

export function downloadRouteGpx(candidate: Candidate): void {
  const blob = new Blob([routeGpx(candidate)], { type: "application/gpx+xml;charset=utf-8" });
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

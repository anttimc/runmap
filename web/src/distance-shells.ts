/** Render distance-band polygons and their shared map legend colors. */
import type { ExpressionSpecification, GeoJSONSource, Map } from "maplibre-gl";
import type { FeatureCollection } from "geojson";
import { turboColors } from "./turbo.ts";

export const distanceBandM = 500;
export const shellLayerIds = ["distance-shell-fill", "distance-shell-boundaries"];

export function distanceBands(
  maximumM: number,
  intervalM = distanceBandM,
): { startM: number; color: string }[] {
  const count = Math.max(1, Math.ceil(maximumM / intervalM));
  return Array.from({ length: count }, (_, index) => {
    const colorIndex =
      count === 1 ? 0 : Math.floor((index * (turboColors.length - 1)) / (count - 1));
    return { startM: index * intervalM, color: turboColors[colorIndex] };
  });
}

export function shellColor(maximumM: number, intervalM = distanceBandM): ExpressionSpecification {
  const bands = distanceBands(maximumM, intervalM);
  return [
    "step",
    ["get", "lower_m"],
    bands[0].color,
    ...bands.slice(1).flatMap((band) => [band.startM, band.color]),
  ] as ExpressionSpecification;
}

export function addShellLayers(map: Map): void {
  const before =
    map.getStyle().layers?.find((layer) => layer.id === "context-road-casing")?.id ??
    map.getStyle().layers?.find((layer) => layer.type === "symbol")?.id;
  map.addSource("distance-shells", {
    type: "geojson",
    data: { type: "FeatureCollection", features: [] },
  });
  map.addLayer(
    {
      id: "distance-shell-fill",
      type: "fill",
      source: "distance-shells",
      layout: { visibility: "none" },
      paint: { "fill-color": shellColor(3000), "fill-opacity": 0.32, "fill-antialias": false },
    },
    before,
  );
  map.addLayer(
    {
      id: "distance-shell-boundaries",
      type: "line",
      source: "distance-shells",
      layout: { visibility: "none" },
      paint: { "line-color": "#263644", "line-width": 0.8, "line-opacity": 0.45 },
    },
    before,
  );
}

export function setShellData(map: Map, data?: FeatureCollection): void {
  (map.getSource("distance-shells") as GeoJSONSource).setData(
    data ?? { type: "FeatureCollection", features: [] },
  );
}

export function showShellLayers(
  map: Map,
  visible: boolean,
  maximumM: number,
  intervalM = distanceBandM,
): void {
  for (const id of shellLayerIds)
    map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
  map.setPaintProperty("distance-shell-fill", "fill-color", shellColor(maximumM, intervalM));
}

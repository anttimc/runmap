/** Verify distance-band colors and shell layer/source lifetimes. */
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  addShellLayers,
  distanceBands,
  setShellData,
  shellColor,
  showShellLayers,
} from "../src/distance-shells.ts";

test("shell colors share the 500 m distance legend, including a shorter final band", () => {
  const bands = distanceBands(1250);
  assert.deepEqual(
    bands.map((band) => band.startM),
    [0, 500, 1000],
  );
  assert.deepEqual(shellColor(1250), [
    "step",
    ["get", "lower_m"],
    bands[0].color,
    500,
    bands[1].color,
    1000,
    bands[2].color,
  ]);
  assert.equal(distanceBands(100).length, 1);
});

test("shell polygons stay below roads and clear without replacing their source", () => {
  const sources = new Map();
  const layers = [];
  const visibility = new Map();
  let data;
  const map = {
    getStyle: () => ({ layers: [{ id: "context-road-casing", type: "line" }] }),
    addSource: (id, specification) => sources.set(id, specification),
    addLayer: (specification, before) => layers.push({ specification, before }),
    getSource: () => ({
      setData: (next) => {
        data = next;
      },
    }),
    setLayoutProperty: (id, property, value) => visibility.set(id + "." + property, value),
    setPaintProperty: () => {},
  };
  addShellLayers(map);
  assert.equal(sources.size, 1);
  assert.ok(layers.every((layer) => layer.before === "context-road-casing"));
  showShellLayers(map, true, 5000);
  assert.equal(visibility.get("distance-shell-fill.visibility"), "visible");
  const shells = { type: "FeatureCollection", features: [{ type: "Feature" }] };
  setShellData(map, shells);
  assert.equal(data, shells);
  setShellData(map);
  assert.deepEqual(data.features, []);
  showShellLayers(map, false, 5000);
  assert.equal(visibility.get("distance-shell-fill.visibility"), "none");
  assert.equal(sources.size, 1);
});

test("extra-travel colors span the observed maximum in 200 m bands", () => {
  const bands = distanceBands(550, 200);
  assert.deepEqual(
    bands.map((band) => band.startM),
    [0, 200, 400],
  );
  assert.deepEqual(shellColor(550, 200), [
    "step",
    ["get", "lower_m"],
    bands[0].color,
    200,
    bands[1].color,
    400,
    bands[2].color,
  ]);
  assert.equal(bands.at(-1).color, distanceBands(3000).at(-1).color);
});

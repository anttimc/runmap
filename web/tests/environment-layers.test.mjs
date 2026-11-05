/** Keep environment layers independent, initially off, and visibly unavailable on failure. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { startEnvironmentLayers } from "../src/environment-layers.ts";

function node() {
  return {
    children: [],
    listeners: {},
    style: {},
    textContent: "",
    append(...children) {
      this.children.push(...children);
    },
    addEventListener(name, handler) {
      this.listeners[name] = handler;
    },
    setAttribute(name, value) {
      this[name] = value;
    },
  };
}

function documentMock(context) {
  const nodes = new Map(
    ["environment-layer-status", "environment-layer-controls", "layer-environment"].map((id) => [
      id,
      node(),
    ]),
  );
  globalThis.document = {
    getElementById: (id) => nodes.get(id),
    createElement: node,
    createTextNode: node,
  };
  context.after(() => delete globalThis.document);
  return nodes;
}

test("missing regional data does not add map sources", async (context) => {
  const nodes = documentMock(context);
  context.mock.method(globalThis, "fetch", async () => new Response("missing", { status: 404 }));
  await startEnvironmentLayers({});
  assert.match(nodes.get("environment-layer-status").textContent, /not been prepared/);
  assert.equal(nodes.get("environment-layer-controls").children.length, 0);
});

test("toggling one layer preserves other layers and opacity", async (context) => {
  const nodes = documentMock(context);
  context.mock.method(globalThis, "fetch", async () =>
    Response.json({
      tile_template: "/tiles/v1/id/{layer}/{z}/{x}/{y}.png",
      tile_size: 256,
      min_zoom: 8,
      max_zoom: 16,
      bounds_wgs84: [24, 60, 25, 61],
    }),
  );
  const layers = [],
    sources = [],
    layout = [],
    paint = [],
    events = {};
  await startEnvironmentLayers({
    addSource: (id, source) => sources.push([id, source]),
    addLayer: (layer, before) => layers.push([layer, before]),
    getLayer: () => true,
    setLayoutProperty: (...args) => layout.push(args),
    setPaintProperty: (...args) => paint.push(args),
    on: (name, handler) => {
      events[name] = handler;
    },
  });
  assert.equal(layers.length, 5);
  assert.ok(
    layers.every(
      ([layer, before]) => layer.layout.visibility === "none" && before === "context-road-casing",
    ),
  );
  assert.ok(sources.every(([, source]) => source.type === "raster" && source.bounds.length === 4));
  const master = nodes.get("layer-environment");
  assert.equal(master.disabled, false);
  assert.equal(master.checked, undefined);
  master.checked = true;
  master.listeners.change();
  assert.deepEqual(layout[0], ["environment-field-greenery", "visibility", "visible"]);
  const [label, opacity, output] = nodes.get("environment-layer-controls").children[1].children;
  const toggle = label.children[0];
  assert.equal(opacity.disabled, true);
  toggle.checked = true;
  toggle.listeners.change();
  opacity.value = "35";
  opacity.listeners.input();
  assert.equal(opacity.disabled, false);
  assert.equal(output.textContent, "35%");
  assert.deepEqual(layout.at(-1), ["environment-field-water", "visibility", "visible"]);
  assert.deepEqual(paint, [["environment-field-water", "raster-opacity", 0.35]]);
  master.checked = false;
  master.listeners.change();
  assert.deepEqual(layout.at(-1), ["environment-field-support", "visibility", "none"]);
  events.error({ sourceId: "environment-field-greenery" });
  assert.match(nodes.get("environment-layer-status").textContent, /failed/);
});

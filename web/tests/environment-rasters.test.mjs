/** Keep raster inspection current during rapid clicks and preserve source-only operation. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { startRasterLayers } from "../src/environment-rasters.ts";

function node() {
  return {
    textContent: "",
    dataset: {},
    style: {},
    append() {},
    after() {},
    addEventListener() {},
  };
}

test("absent raster bundles leave source inspection usable", async (context) => {
  const status = node();
  globalThis.document = { getElementById: () => status };
  context.after(() => delete globalThis.document);
  context.mock.method(globalThis, "fetch", async () => new Response("missing", { status: 404 }));
  assert.equal(await startRasterLayers({}, "source"), undefined);
  assert.match(status.textContent, /Source inspection remains available/);
});

test("a late raster response cannot replace a newer clicked cell", async (context) => {
  const nodes = new Map(
    ["raster-status", "raster-layers", "raster-selection"].map((id) => [id, node()]),
  );
  globalThis.document = {
    getElementById: (id) => nodes.get(id),
    createElement: node,
    createTextNode: node,
  };
  context.after(() => delete globalThis.document);
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url, options) => {
    if (url.includes("/config?"))
      return Response.json({
        manifest: {
          dataset_id: "r",
          config: { resolution_m: 10 },
          method: "baseline",
          limitations: [],
          previews: {
            coordinates: [
              [0, 1],
              [1, 1],
              [1, 0],
              [0, 0],
            ],
          },
        },
        image_template: "/r/{layer}.png",
        sample_url: "/sample?dataset=r",
      });
    return await new Promise((resolve) => requests.push({ resolve, signal: options.signal }));
  });
  const layers = [];
  const controls = await startRasterLayers(
    {
      on() {},
      addSource() {},
      getLayer: () => true,
      addLayer: (layer) => layers.push(layer),
    },
    "source",
  );
  assert.equal(layers.length, 5);
  assert.ok(layers.every((layer) => layer.layout.visibility === "none"));
  controls.inspect({ lng: 24, lat: 60 });
  controls.inspect({ lng: 25, lat: 60 });
  assert.equal(requests[0].signal.aborted, true);
  requests[1].resolve(Response.json({ status: "outside_pilot", values: null }));
  for (let i = 0; i < 20; i++) await Promise.resolve();
  assert.match(nodes.get("raster-selection").textContent, /Outside the prepared pilot/);
  requests[0].resolve(new Response("outdated", { status: 409 }));
  for (let i = 0; i < 20; i++) await Promise.resolve();
  assert.match(nodes.get("raster-selection").textContent, /Outside the prepared pilot/);
});

test("park coverage stays diagnostic even when advertised by the bundle", async (context) => {
  globalThis.document = { getElementById: node, createElement: node, createTextNode: node };
  context.after(() => delete globalThis.document);
  let advertised = ["greenery", "park", "water", "traffic", "wetland", "evidence_gap"];
  context.mock.method(globalThis, "fetch", async () =>
    Response.json({
      manifest: {
        dataset_id: "p",
        method: "baseline",
        config: { resolution_m: 10 },
        limitations: [],
        previews: {
          coordinates: [
            [0, 1],
            [1, 1],
            [1, 0],
            [0, 0],
          ],
          layers: advertised,
        },
      },
      image_template: "/p/{layer}.png",
      sample_url: "/sample?dataset=p",
    }),
  );
  const sources = [];
  const map = { on() {}, getLayer: () => true, addSource: (id) => sources.push(id), addLayer() {} };
  await startRasterLayers(map, "source");
  assert.equal(sources.includes("raster-park"), false);
  assert.ok(sources.includes("raster-greenery"));
  assert.equal(sources.includes("raster-forest"), false);
  advertised = advertised.filter((id) => id !== "park");
  sources.length = 0;
  await startRasterLayers(map, "source");
  assert.equal(sources.includes("raster-park"), false);
});

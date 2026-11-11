/** Source lights remain inspectable without depending on routing charges or raster data. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { signalDetails, startTrafficLights } from "../src/traffic-lights.ts";

function node() {
  return {
    children: [],
    listeners: {},
    style: {},
    textContent: "",
    open: false,
    showPopover() {
      this.open = true;
    },
    hidePopover() {
      this.open = false;
    },
    matches() {
      return this.open;
    },
    getBoundingClientRect() {
      return { width: 200, height: 60 };
    },
    append(...children) {
      this.children.push(...children);
    },
    addEventListener(name, handler) {
      this.listeners[name] = handler;
    },
  };
}

function fixture(context) {
  const nodes = new Map(
    [
      "traffic-light-status",
      "traffic-light-help",
      "traffic-light-point-popup",
      "environment-layer-controls",
      "layer-environment",
    ].map((id) => [id, node()]),
  );
  nodes.get("layer-environment").disabled = true;
  globalThis.document = {
    getElementById: (id) => nodes.get(id),
    createElement: node,
    createTextNode: node,
  };
  context.after(() => delete globalThis.document);
  globalThis.innerWidth = 1024;
  globalThis.innerHeight = 768;
  context.after(() => {
    delete globalThis.innerWidth;
    delete globalThis.innerHeight;
  });
  const layers = [],
    sources = [],
    layout = [],
    updates = [],
    events = new Map();
  let zoom = 15;
  const map = {
    addSource: (...args) => sources.push(args),
    addLayer: (layer) => layers.push(layer),
    setLayoutProperty: (...args) => layout.push(args),
    getSource: () => ({ setData: (data) => updates.push(data) }),
    getZoom: () => zoom,
    getBounds: () => ({
      getWest: () => 24.8,
      getSouth: () => 60.2,
      getEast: () => 24.9,
      getNorth: () => 60.3,
    }),
    on: (name, layer, handler) =>
      events.set(typeof layer === "string" ? `${name}:${layer}` : name, handler ?? layer),
  };
  return {
    nodes,
    map,
    layers,
    sources,
    layout,
    updates,
    events,
    setZoom: (value) => {
      zoom = value;
    },
  };
}

const config = { dataset_id: "network", url: "/api/paths/signals", minimum_zoom: 13 };
const settled = () => new Promise((resolve) => setImmediate(resolve));
const collection = (id) => ({
  type: "FeatureCollection",
  status: "ready",
  features: [{ type: "Feature", id }],
});

test("unavailable evidence reports its absence without markers", async (context) => {
  const f = fixture(context);
  context.mock.method(globalThis, "fetch", async () => new Response("missing", { status: 404 }));
  await startTrafficLights(f.map);
  assert.equal(f.sources.length, 0);
  assert.match(f.nodes.get("traffic-light-status").textContent, /unavailable/);
  assert.equal(f.nodes.get("layer-environment").disabled, true);
});

test("traffic-light evidence toggles with Environment and loads only its viewport", async (context) => {
  const f = fixture(context),
    urls = [];
  context.mock.method(globalThis, "fetch", async (url) => {
    urls.push(url);
    return Response.json(url.endsWith("/config") ? config : collection("pedestrian"));
  });
  await startTrafficLights(f.map);
  assert.equal(f.layers[0].type, "circle");
  assert.equal(f.layers[0].layout.visibility, "none");
  assert.equal(f.sources[0][1].type, "geojson");
  const toggle = f.nodes.get("environment-layer-controls").children[0].children[0].children[0];
  const master = f.nodes.get("layer-environment");
  toggle.checked = true;
  toggle.listeners.change();
  await settled();
  assert.equal(urls.length, 1);
  master.checked = true;
  master.listeners.change();
  await settled();
  assert.match(urls[1], /dataset=network&west=24.8&south=60.2&east=24.9&north=60.3/);
  assert.equal(f.updates.at(-1).features[0].id, "pedestrian");
  assert.equal(f.nodes.get("traffic-light-status").textContent, "1 signal markers");
  assert.equal(f.nodes.get("traffic-light-help").hidden, false);
  f.events.get("mousemove:traffic-light-evidence")({
    originalEvent: { clientX: 100, clientY: 200 },
    features: [
      {
        properties: {
          kind: "pedestrian",
          osm_id: "42",
          tags: { "crossing:signals": "yes" },
        },
      },
    ],
  });
  assert.match(
    f.nodes.get("traffic-light-point-popup").textContent,
    /OSM node 42.*crossing:signals=yes/,
  );
  assert.equal(f.nodes.get("traffic-light-point-popup").open, true);
  assert.equal(f.nodes.get("traffic-light-status").textContent, "1 signal markers");
  f.events.get("mouseleave:traffic-light-evidence")();
  assert.equal(f.nodes.get("traffic-light-point-popup").open, false);
  f.events.get("click:traffic-light-evidence")({
    originalEvent: { clientX: 100, clientY: 200 },
    features: [{ properties: { osm_id: "42", kind: "pedestrian", tags: {} } }],
  });
  f.events.get("mouseleave:traffic-light-evidence")();
  assert.equal(f.nodes.get("traffic-light-point-popup").open, true);
  f.setZoom(12);
  f.events.get("moveend")();
  await settled();
  assert.equal(urls.length, 2);
  assert.equal(f.updates.at(-1).features.length, 0);
  assert.match(f.nodes.get("traffic-light-status").textContent, /Zoom in/);
  master.checked = false;
  master.listeners.change();
  assert.deepEqual(f.layout.at(-1), ["traffic-light-evidence", "visibility", "none"]);
  assert.equal(f.nodes.get("traffic-light-status").hidden, true);
});

test("an obsolete viewport response cannot replace newer traffic-light evidence", async (context) => {
  const f = fixture(context),
    pending = [];
  context.mock.method(globalThis, "fetch", async (url, options) => {
    if (url.endsWith("/config")) return Response.json(config);
    return new Promise((resolve) => pending.push({ resolve, signal: options.signal }));
  });
  await startTrafficLights(f.map);
  const toggle = f.nodes.get("environment-layer-controls").children[0].children[0].children[0];
  f.nodes.get("layer-environment").checked = toggle.checked = true;
  toggle.listeners.change();
  f.events.get("moveend")();
  assert.equal(pending[0].signal.aborted, true);
  pending[1].resolve(Response.json(collection("new")));
  await settled();
  pending[0].resolve(Response.json(collection("old")));
  await settled();
  assert.equal(f.updates.at(-1).features[0].id, "new");
});

test("inspection distinguishes generic signal evidence and tolerates missing details", () => {
  assert.match(
    signalDetails({ osm_id: "1", kind: "uncertain", tags: '{"highway":"traffic_signals"}' }),
    /Other or uncertain signal.*highway=traffic_signals/,
  );
  assert.match(signalDetails({ osm_id: "1", tags: "null" }), /OSM node 1/);
  assert.match(signalDetails({ osm_id: "1", tags: "bad json" }), /OSM node 1/);
});

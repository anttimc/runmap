/** Check snapshot reuse and obsolete-response suppression during automatic updates. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { startTurnaroundRegion } from "../src/turnaround.ts";

const flush = async () => {
  for (let i = 0; i < 30; i++) await Promise.resolve();
};

test("width changes reuse snapshots and discard stale regions", async (context) => {
  const nodes = new Map([
    ["run-target-distance", { value: "10", checkValidity: () => true }],
    ["run-target-enabled", { checked: true }],
    ["run-target-manual", { checked: false }],
    ["run-turnaround-method", {}],
    ["run-target-controls", {}],
    ["run-turnaround-row", {}],
    ["run-target-status", { textContent: "" }],
  ]);
  for (const node of nodes.values()) node.addEventListener = () => {};
  globalThis.document = { getElementById: (id) => nodes.get(id) };
  context.after(() => {
    delete globalThis.document;
  });
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const layers = [];
  let displayed;
  const map = {
    getStyle: () => ({ layers: [{ id: "context-road-casing", type: "line" }] }),
    addSource: () => {},
    addLayer: (layer, before) => layers.push({ layer, before }),
    getSource: () => ({
      setData: (data) => {
        displayed = data;
      },
    }),
  };
  let snapshots = 0;
  let completeOld;
  let oldSignal;
  context.mock.method(globalThis, "fetch", async (url, options) => {
    if (url === "/api/paths/snapshots") {
      snapshots++;
      return Response.json({
        dataset_id: "d",
        snapshot_id: "s",
        expires_at: Date.now() / 1000 + 300,
      });
    }
    const width = Number(new URL(url, "http://local").searchParams.get("width"));
    const payload = {
      type: "FeatureCollection",
      features: [{ properties: { width } }],
      dataset_id: "d",
      snapshot_id: "s",
      target_m: 10000,
      width,
      model: width === 0.4 ? "ellipse-extra-v1" : "cubic-extra-v1",
      uncertainty: { method: "held-start-residual-v1" },
    };
    if (width === 0.18) {
      oldSignal = options.signal;
      return new Promise((resolve) => {
        completeOld = () => resolve(Response.json(payload));
      });
    }
    return Response.json(payload);
  });
  const state = {
    plannerMode: "round_trip",
    home: [24.8, 60.2],
    policy: { surface: "all", avoid_stairs: true, radius_m: 50 },
  };
  let width = 0.18;
  let ready = false;
  const refresh = startTurnaroundRegion(map, {
    datasetId: "d",
    supported: true,
    state,
    width: () => width,
    hills: () => false,
    selectionChanged: (value) => {
      ready = value;
    },
  });
  refresh();
  assert.equal(ready, false);
  assert.equal(nodes.get("run-turnaround-row").hidden, true);
  context.mock.timers.tick(300);
  await flush();
  assert.equal(snapshots, 1);
  width = 0.3;
  refresh();
  assert.equal(oldSignal.aborted, true);
  context.mock.timers.tick(300);
  await flush();
  assert.equal(snapshots, 1);
  assert.equal(displayed.width, 0.3);
  assert.equal(ready, true);
  assert.equal(nodes.get("run-turnaround-row").hidden, false);
  completeOld();
  await flush();
  assert.equal(displayed.width, 0.3);
  width = 0.4;
  refresh();
  context.mock.timers.tick(300);
  await flush();
  assert.match(nodes.get("run-target-status").textContent, /model changed/);
  assert.deepEqual(displayed.features, []);
  state.plannerMode = "one_way";
  refresh();
  assert.deepEqual(displayed.features, []);
  assert.ok(layers.every(({ before }) => before === "context-road-casing"));
});

test("manual selection starts after the start by default; estimation requires opting in and entering kilometres", (context) => {
  const nodes = new Map([
    [
      "run-target-distance",
      {
        value: "",
        checkValidity: () => true,
        focus: () => {
          focused = true;
        },
      },
    ],
    ["run-target-enabled", { checked: false }],
    ["run-target-manual", { checked: true }],
    ["run-target-status", { textContent: "" }],
    ["run-turnaround-method", {}],
    ["run-target-controls", {}],
    ["run-turnaround-row", {}],
  ]);
  let focused = false;
  const listeners = new Map();
  for (const [id, node] of nodes)
    node.addEventListener = (event, callback) => {
      const key = `${id}:${event}`;
      listeners.set(key, [...(listeners.get(key) ?? []), callback]);
    };
  globalThis.document = { getElementById: (id) => nodes.get(id) };
  context.after(() => {
    delete globalThis.document;
  });
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const fetch = context.mock.method(globalThis, "fetch", () => {
    throw new Error("Unexpected request");
  });
  const map = {
    getStyle: () => ({ layers: [] }),
    addSource: () => {},
    addLayer: () => {},
    getSource: () => ({ setData: () => {} }),
  };
  const state = { plannerMode: "round_trip", home: undefined, policy: {} };
  let ready = false;
  const refresh = startTurnaroundRegion(map, {
    datasetId: "d",
    supported: true,
    state,
    width: () => 0.18,
    hills: () => false,
    selectionChanged: (value) => {
      ready = value;
    },
  });
  refresh();
  assert.equal(nodes.get("run-turnaround-method").hidden, true);
  assert.equal(nodes.get("run-target-status").textContent, "");
  state.home = [24.8, 60.2];
  refresh();
  assert.equal(nodes.get("run-turnaround-method").hidden, false);
  assert.equal(nodes.get("run-target-controls").hidden, true);
  assert.equal(ready, true);
  assert.equal(nodes.get("run-turnaround-row").hidden, false);
  nodes.get("run-target-manual").checked = false;
  nodes.get("run-target-enabled").checked = true;
  for (const callback of listeners.get("run-target-enabled:change")) callback();
  assert.equal(focused, true);
  assert.equal(nodes.get("run-target-controls").hidden, false);
  assert.equal(ready, false);
  assert.equal(nodes.get("run-target-status").textContent, "");
  context.mock.timers.tick(1000);
  assert.equal(fetch.mock.callCount(), 0);
  nodes.get("run-target-enabled").checked = false;
  nodes.get("run-target-manual").checked = true;
  for (const callback of listeners.get("run-target-manual:change")) callback();
  assert.equal(ready, true);
  assert.equal(nodes.get("run-target-controls").hidden, true);
  assert.equal(nodes.get("run-turnaround-row").hidden, false);
  assert.equal(fetch.mock.callCount(), 0);
});

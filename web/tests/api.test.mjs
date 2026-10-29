/** Verify HTTP transport, cancellation, and actionable API errors. */
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ApiError,
  loadDistanceShells,
  loadMapConfig,
  loadInspection,
  prepareSnapshot,
} from "../src/api.ts";

test("HTTP failures preserve actionable server details and handle non-JSON bodies", async (context) => {
  const responses = [
    new Response(JSON.stringify({ detail: "Run planner is busy; try again shortly" }), {
      status: 429,
    }),
    new Response("Proxy unavailable", { status: 503 }),
    new Response(JSON.stringify({ detail: [{ msg: "Invalid location" }] }), { status: 422 }),
    new Response(JSON.stringify({ detail: "Old dataset" }), { status: 409 }),
  ];
  context.mock.method(globalThis, "fetch", async () => responses.shift());

  await assert.rejects(loadMapConfig(), /Run planner is busy/);
  await assert.rejects(loadMapConfig(), /Request failed \(503\)/);
  await assert.rejects(loadMapConfig(), /Request failed \(422\)/);
  await assert.rejects(loadMapConfig(), /Dataset changed — reload the map/);
});

test("inspection cancellation reaches fetch and retains AbortError identity", async (context) => {
  const controller = new AbortController();
  context.mock.method(globalThis, "fetch", (_url, options) => {
    assert.equal(options.signal, controller.signal);
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
    });
  });

  const pending = loadInspection("segment", new URLSearchParams(), controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
});

test("snapshot transport preserves server responses and request cancellation", async (context) => {
  const controller = new AbortController();
  const snapshot = { status: "ready", dataset_id: "dataset", snapshot_id: "snapshot" };
  context.mock.method(globalThis, "fetch", async (_url, options) => {
    assert.equal(options.signal, controller.signal);
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Content-Type"], "application/json");
    const body = JSON.parse(options.body);
    assert.equal(body.budget_km, 3);
    assert.equal(body.avoid_stairs, true);
    return Response.json(snapshot);
  });

  const result = await prepareSnapshot(
    {
      dataset_id: "dataset",
      longitude: 24.795,
      latitude: 60.216,
      budget_km: 3,
      surface: "all",
      avoid_stairs: true,
      radius_m: 50,
    },
    controller.signal,
  );
  assert.deepEqual(result, snapshot);
});

test("shell transport selects a method on the same snapshot and preserves expiry status", async (context) => {
  const controller = new AbortController();
  const shells = { type: "FeatureCollection", features: [], snapshot_id: "snapshot" };
  context.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(options.signal, controller.signal);
    const parsed = new URL(url, "http://localhost");
    assert.equal(parsed.pathname, "/api/paths/snapshots/snapshot/shells");
    assert.equal(parsed.searchParams.get("dataset_id"), "dataset");
    assert.equal(parsed.searchParams.get("method"), "triangulation");
    assert.equal(parsed.searchParams.get("smoothing_m"), "50");
    return Response.json(shells);
  });
  assert.deepEqual(
    await loadDistanceShells("dataset", "snapshot", "triangulation", controller.signal, false, 50),
    shells,
  );
  context.mock.method(globalThis, "fetch", async () =>
    Response.json({ detail: "Snapshot expired" }, { status: 410 }),
  );
  await assert.rejects(
    loadDistanceShells("dataset", "snapshot", "grid", controller.signal),
    (error) => error instanceof ApiError && error.status === 410 && /expired/.test(error.message),
  );
});

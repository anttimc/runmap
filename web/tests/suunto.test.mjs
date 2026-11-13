/** Verify dialog snapshots and lost-response recovery without external services. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { operationMessage, startSuuntoExport } from "../src/suunto.ts";

const original = {
  mode: "one_way",
  distance_m: 1000,
  legs: [
    {
      coordinates: [
        [24.8, 60.2],
        [24.81, 60.21],
      ],
      traversals: [],
    },
  ],
};
const flush = () => new Promise((resolve) => setImmediate(resolve));

function setup(context, handler) {
  const elements = new Map();
  const stored = new Map();
  const ids = [
    "run-suunto",
    "suunto-dialog",
    "suunto-name",
    "suunto-watch",
    "suunto-send",
    "suunto-refresh",
    "suunto-select",
    "suunto-new",
    "suunto-status",
    "suunto-close",
  ];
  for (const id of ids) {
    const listeners = new Map();
    elements.set(id, {
      value: "",
      hidden: true,
      disabled: false,
      checked: true,
      textContent: "",
      addEventListener: (name, callback) => listeners.set(name, callback),
      setAttribute() {},
      showModal() {},
      close() {},
      click: () => listeners.get("click")?.(),
      input: () => listeners.get("input")?.(),
    });
  }
  context.mock.method(globalThis, "fetch", handler);
  globalThis.document = { getElementById: (id) => elements.get(id) };
  globalThis.localStorage = {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
    removeItem: (key) => stored.delete(key),
  };
  context.after(() => {
    delete globalThis.document;
    delete globalThis.localStorage;
  });
  return elements;
}

test("dialog sends the captured route and uses the operation ID to recover a lost response", async (context) => {
  const requests = [];
  let id;
  const elements = setup(context, async (url, options) => {
    requests.push([url, options]);
    if (url.endsWith("/suunto")) return Response.json({ configured: true });
    if (url.endsWith("/profile")) return Response.json({ dataset_id: "dataset", intervals: [] });
    if (options?.method === "POST") {
      id = JSON.parse(options.body).operation_id;
      throw new Error("Lost response");
    }
    return Response.json({
      id,
      name: "Recovered route",
      use_in_watch: true,
      state: "selected",
      error: null,
    });
  });
  let candidate = original;
  startSuuntoExport("dataset", () => candidate);
  await flush();
  assert.equal(elements.get("run-suunto").hidden, false);
  elements.get("run-suunto").click();
  candidate = { ...original, distance_m: 9000 };
  elements.get("suunto-send").click();
  await flush();
  const body = JSON.parse(requests.find(([url]) => url.endsWith("/imports"))[1].body);
  assert.match(body.gpx, /1.00 km/);
  assert.equal(body.use_in_watch, true);
  assert.equal(elements.get("suunto-send").disabled, true);
  elements.get("suunto-refresh").click();
  await flush();
  assert.match(elements.get("suunto-status").textContent, /Selected for watch sync/);
  assert.equal(elements.get("suunto-name").value, "Recovered route");
  assert.equal(
    requests.filter(
      ([, options]) => options?.method === "POST" && options?.headers?.["X-Runmap-Action"],
    ).length,
    1,
  );
});

test("a restored creation awaiting watch selection offers selection-only retry", async (context) => {
  const id = "00000000-0000-4000-8000-000000000001";
  const requests = [];
  const elements = setup(context, async (url, options) => {
    requests.push([url, options]);
    if (url.endsWith("/suunto")) return Response.json({ configured: true });
    return Response.json({
      id,
      name: "Earlier route",
      use_in_watch: true,
      state: options ? "selected" : "created",
      error: null,
    });
  });
  localStorage.setItem("runmap.suunto.operation", id);
  startSuuntoExport("dataset", () => original);
  await flush();
  elements.get("run-suunto").click();
  await flush();
  assert.equal(elements.get("suunto-select").hidden, false);
  assert.equal(elements.get("suunto-watch").checked, true);
  assert.equal(elements.get("suunto-name").disabled, true);
  elements.get("suunto-select").click();
  await flush();
  const writes = requests.filter(([, options]) => options?.method === "POST");
  assert.equal(writes.length, 1);
  assert.equal(writes[0][0], `/api/integrations/suunto/imports/${id}/select`);
  assert.match(elements.get("suunto-status").textContent, /Selected for watch sync/);
});

for (const [state, useInWatch] of [
  ["selected", true],
  ["created", false],
]) {
  test(`reopening a completed ${state} export allows a new route name`, async (context) => {
    const id = "00000000-0000-4000-8000-000000000001";
    const writes = [];
    const elements = setup(context, async (url, options) => {
      if (url.endsWith("/suunto")) return Response.json({ configured: true });
      if (url.endsWith("/profile")) return Response.json({ dataset_id: "dataset", intervals: [] });
      if (options) {
        const body = JSON.parse(options.body);
        writes.push(body);
        return Response.json({ id: body.operation_id, ...body, state: "selected", error: null });
      }
      return Response.json({
        id,
        name: "Earlier route",
        use_in_watch: useInWatch,
        state,
        error: null,
      });
    });
    localStorage.setItem("runmap.suunto.operation", id);
    startSuuntoExport("dataset", () => original);
    await flush();
    elements.get("run-suunto").click();
    await flush();
    const name = elements.get("suunto-name");
    assert.equal(name.disabled, false);
    assert.equal(elements.get("suunto-send").disabled, false);
    assert.equal(localStorage.getItem("runmap.suunto.operation"), null);
    name.value = "Evening seaside run";
    name.input();
    elements.get("suunto-send").click();
    await flush();
    assert.equal(writes.length, 1);
    assert.equal(writes[0].name, "Evening seaside run");
    assert.notEqual(writes[0].operation_id, id);
  });
}

test("an unconfirmed export stays locked until starting a new export", async (context) => {
  const id = "00000000-0000-4000-8000-000000000001";
  const elements = setup(context, async (url) => {
    if (url.endsWith("/suunto")) return Response.json({ configured: true });
    return Response.json({
      id,
      name: "Unconfirmed route",
      use_in_watch: true,
      state: "post-pending",
    });
  });
  localStorage.setItem("runmap.suunto.operation", id);
  startSuuntoExport("dataset", () => original);
  await flush();
  elements.get("run-suunto").click();
  await flush();
  assert.equal(elements.get("suunto-name").disabled, true);
  assert.equal(localStorage.getItem("runmap.suunto.operation"), id);
  assert.match(elements.get("suunto-status").textContent, /Check your Suunto library/);
  elements.get("suunto-new").click();
  assert.equal(elements.get("suunto-name").disabled, false);
  assert.equal(elements.get("suunto-send").disabled, false);
  assert.equal(localStorage.getItem("runmap.suunto.operation"), null);
});

test("uncertain creation explains manual inspection and never claims watch delivery", () => {
  assert.match(operationMessage({ state: "post-pending" }), /Check your Suunto library/);
  assert.match(operationMessage({ state: "created-unverified" }), /before starting another export/);
  assert.match(operationMessage({ state: "selected" }), /Sync your Suunto app/);
});

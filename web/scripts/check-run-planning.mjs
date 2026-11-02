/** Exercise planner modes and result lifetimes in a browser with controlled route responses. */
// Requires Vite on 5173, the API on 8000, and headless Chrome on debugger port 9232.
// Route responses are controlled fixtures; map setup, snapping and layers use the local API.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const directory = process.argv[2] ?? "/private/tmp/runmap-planning-check";
fs.mkdirSync(directory, { recursive: true });
const tab = await (
  await fetch(`http://127.0.0.1:${process.env.RUNMAP_DEBUG_PORT ?? 9232}/json/new?about:blank`, {
    method: "PUT",
  })
).json();
const socket = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((resolve) => socket.addEventListener("open", resolve));
let sequence = 0;
let holdRun = false;
let heldRun;
let runError = false;
const pending = new Map();
const requests = [];
const exceptions = [];
let profileRequests = 0;

async function answerProfile(event) {
  profileRequests++;
  const request = JSON.parse(event.request.postData);
  const intervals = [0, 1, 2].map((index) => ({
    start_m: index * 400,
    end_m: (index + 1) * 400,
    start_elevation_m: index === 1 ? null : 20 + index * 4,
    end_elevation_m: index === 1 ? null : 24 + index * 4,
    grade_percent: index === 1 ? null : 1,
    coordinates: [
      [24.795 + index * 0.005, 60.216],
      [24.8 + index * 0.005, 60.216],
    ],
    quality_flags: index === 1 ? ["missing_terrain"] : index === 2 ? ["bridge"] : [],
  }));
  await call("Fetch.fulfillRequest", {
    requestId: event.requestId,
    responseCode: 200,
    responseHeaders: [{ name: "Content-Type", value: "application/json" }],
    body: Buffer.from(
      JSON.stringify({
        dataset_id: request.dataset_id,
        manifest_id: "fixture",
        wavelength_m: 40,
        sampling_interval_m: 10,
        distance_m: 1200,
        turnaround_m: request.legs.length === 2 ? 600 : null,
        ascent_m: 8,
        descent_m: 0,
        valid_length_fraction: 2 / 3,
        totals_complete: false,
        intervals,
      }),
    ).toString("base64"),
  });
}

function call(method, params = {}) {
  const id = ++sequence;
  const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  socket.send(JSON.stringify({ id, method, params }));
  return result;
}

function routeResponse(request) {
  const { mode, dataset_id, home, turnaround } = request;
  const coordinates = [
    [home.longitude, home.latitude],
    [turnaround.longitude, turnaround.latitude],
  ];
  const observations = {
    estimated_ascent_m: null,
    estimated_descent_m: null,
    terrain_valid_fraction: null,
  };
  const outbound = {
    coordinates,
    distance_m: 1200,
    observations,
    traversals: [{ segment_id: "s", start_m: 0, end_m: 1200 }],
  };
  const back = {
    coordinates: [...coordinates].reverse(),
    distance_m: 1200,
    observations,
    traversals: [{ segment_id: "s", start_m: 1200, end_m: 0 }],
  };
  const base = {
    mode,
    legs: [outbound],
    outbound_left: null,
    closest_centre_m: null,
    encloses_centre: null,
    distance_m: 1200,
    repeated_distance_m: 0,
  };
  let candidates =
    mode === "one_way"
      ? [base]
      : [true, false].map((left) => ({
          ...base,
          legs: [outbound, { ...back, distance_m: left ? 1200 : 1400 }],
          outbound_left: left,
          closest_centre_m: 0,
          distance_m: left ? 2400 : 2600,
          repeated_distance_m: 1200,
        }));
  if (request.settings.avoidance_ratio === 0) candidates = candidates.slice(0, 1);
  return {
    mode,
    cost_model: { shape_settings: request.settings },
    dataset_id,
    status: "complete",
    candidates,
    home: null,
    turnaround: null,
    elapsed_s: 0.01,
    coverage_note: "Browser fixture",
  };
}

async function answerRun(event) {
  const request = JSON.parse(event.request.postData);
  await call("Fetch.fulfillRequest", {
    requestId: event.requestId,
    responseCode: runError ? 429 : 200,
    responseHeaders: [{ name: "Content-Type", value: "application/json" }],
    body: Buffer.from(
      JSON.stringify(runError ? { detail: "Planner fixture busy" } : routeResponse(request)),
    ).toString("base64"),
  });
}

socket.addEventListener("message", async (event) => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const task = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) task.reject(new Error(message.error.message));
    else task.resolve(message.result);
    return;
  }
  if (message.method === "Runtime.exceptionThrown")
    exceptions.push(message.params.exceptionDetails);
  if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") {
    console.error(
      "Browser:",
      message.params.args.map((arg) => arg.description ?? arg.value).join(" "),
    );
  }
  if (message.method !== "Fetch.requestPaused") return;
  try {
    const event = message.params;
    if (event.request.url.includes("/api/paths/run/profile")) {
      await answerProfile(event);
    } else if (event.request.url.includes("/api/paths/run")) {
      requests.push(JSON.parse(event.request.postData));
      if (holdRun) heldRun = event;
      else await answerRun(event);
    } else {
      const response = await call("Fetch.getResponseBody", { requestId: event.requestId });
      const source = response.base64Encoded
        ? Buffer.from(response.body, "base64").toString()
        : response.body;
      await call("Fetch.fulfillRequest", {
        requestId: event.requestId,
        responseCode: 200,
        responseHeaders: [{ name: "Content-Type", value: "text/javascript" }],
        body: Buffer.from(
          source + '\nObject.defineProperty(window, "__map", {get: () => map});',
        ).toString("base64"),
      });
    }
  } catch (error) {
    exceptions.push(String(error));
  }
});

async function evaluate(expression) {
  const result = await call("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}

async function until(expression) {
  for (let count = 0; count < 200; count++) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const state = await evaluate(`({ status: document.getElementById('status')?.textContent,
    path: document.getElementById('path-status')?.textContent, map: !!window.__map })`);
  throw new Error(`Timed out: ${expression}; ${JSON.stringify(state)}`);
}

async function mode(value) {
  await evaluate(`document.getElementById('plan-mode').value = '${value}';
    document.getElementById('plan-mode').dispatchEvent(new Event('change'));`);
}

async function view() {
  return evaluate(`({
    controls: !document.getElementById('run-controls').hidden,
    footer: !document.getElementById('run-footer').hidden,
    shape: !document.getElementById('run-shape-controls').hidden,
    endpoint: document.getElementById('run-endpoint-label').textContent,
    summary: document.getElementById('run-candidates').textContent,
    results: !document.getElementById('run-results').hidden,
    status: document.getElementById('run-status').textContent,
    features: window.__map.getSource('run-geometry').serialize().data.features,
    layers: ['layer-background', 'layer-path', 'layer-distance'].map(id => document.getElementById(id).checked)
  })`);
}

async function screenshot(name) {
  const result = await call("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(path.join(directory, name + ".png"), Buffer.from(result.data, "base64"));
}

try {
  await call("Page.enable");
  await call("Runtime.enable");
  await call("Network.enable");
  await call("Network.setCacheDisabled", { cacheDisabled: true });
  await call("Page.bringToFront");
  await call("Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await call("Fetch.enable", {
    patterns: [
      { urlPattern: "*/src/main.ts*", requestStage: "Response" },
      { urlPattern: "*/api/paths/run", requestStage: "Request" },
      { urlPattern: "*/api/paths/run/profile", requestStage: "Request" },
    ],
  });
  await call("Page.navigate", { url: process.env.RUNMAP_CHECK_URL ?? "http://127.0.0.1:5173/" });
  await until(`window.__map && !document.getElementById('plan-mode').disabled`);
  assert.equal(await evaluate(`document.getElementById('plan-mode').value`), "off");
  assert.equal((await view()).controls, false);
  await mode("one_way");
  assert.equal((await view()).endpoint, "Destination");
  assert.equal((await view()).shape, false);
  await evaluate(`for (const [lng, lat] of [[24.795, 60.216], [24.815, 60.225]]) {
    window.__map.fire('click', {lngLat: {lng, lat}, point: window.__map.project([lng, lat]), originalEvent: new MouseEvent('click')});
  }; void 0;`);
  await until(`!document.getElementById('run-generate').disabled`);
  await evaluate(`document.getElementById('run-generate').click()`);
  await until(`document.getElementById('run-candidates').textContent.includes('1.20')`);
  assert.equal(requests.at(-1).mode, "one_way");
  assert.equal(requests.at(-1).settings.avoidance_ratio, 0);
  assert.equal(
    (await view()).features.some((f) => f.properties.kind === "centre"),
    false,
  );
  await screenshot("one-way-desktop");

  assert.equal(await evaluate(`!!document.getElementById('path-inspection')`), false);
  await evaluate(`document.getElementById('run-elevation').click()`);
  await until(`!!document.querySelector('#route-profile-content svg')`);
  assert.equal(profileRequests, 1);
  assert.match(
    await evaluate(`document.getElementById('route-profile-content').textContent`),
    /67% elevation coverage/,
  );
  await evaluate(`document.querySelector('#route-profile-content input').focus()`);
  assert.equal(
    await evaluate(
      `document.querySelector('.maplibregl-marker[title="Elevation profile location"]') !== null`,
    ),
    true,
  );
  await screenshot("route-elevation-desktop");
  await evaluate(`const slider = document.querySelector('#route-profile-content input');
    slider.value = '1'; slider.dispatchEvent(new Event('input'));`);
  assert.match(
    await evaluate(`document.querySelector('#route-profile-content .sample-readout').textContent`),
    /Elevation unavailable/,
  );
  await evaluate(`document.getElementById('run-elevation').click()`);
  await evaluate(`const point = window.__map.project([24.805,60.2205]);
    window.__map.fire('click', {lngLat:{lng:24.805,lat:60.2205},point,originalEvent:new MouseEvent('click')}); void 0;`);
  await until(`!document.getElementById('route-inspection').hidden`);
  assert.equal(profileRequests, 1, "route click reuses cached elevation");

  await evaluate(`for (const id of ['layer-path', 'layer-distance']) {
    const input = document.getElementById(id); input.checked = true; input.dispatchEvent(new Event('change'));
  }`);
  await mode("round_trip");
  assert.equal(await evaluate(`document.getElementById('route-inspection').hidden`), true);
  const round = await view();
  assert.equal(round.endpoint, "Turnaround");
  assert.equal(round.shape, true);
  assert.equal(round.results, false);
  assert.equal(
    round.features.some((f) => f.properties.kind === "centre"),
    true,
  );
  assert.deepEqual(round.layers, [true, true, true]);
  await evaluate(`document.getElementById('run-orientation').value = 'counter-clockwise';
    document.getElementById('run-orientation').dispatchEvent(new Event('change'));
    document.getElementById('run-generate').click();`);
  await until(`document.getElementById('run-candidates').textContent.includes('2.60')`);
  assert.equal(requests.at(-1).mode, "round_trip");
  assert.equal(requests.at(-1).settings.avoidance_ratio, 0.18);
  const count = requests.length;
  await evaluate(`document.getElementById('run-orientation').value = 'clockwise';
    document.getElementById('run-orientation').dispatchEvent(new Event('change'));`);
  assert.match((await view()).summary, /2.40/);
  assert.equal(requests.length, count);
  await evaluate(`document.getElementById('run-avoidance').value = '0';
    document.getElementById('run-avoidance').dispatchEvent(new Event('input'));
    document.getElementById('run-orientation').value = 'counter-clockwise';
    document.getElementById('run-orientation').dispatchEvent(new Event('change'));
    document.getElementById('run-generate').click();`);
  await until(`document.getElementById('run-status').textContent === 'Run ready.'`);
  assert.match((await view()).summary, /2.40/);
  await mode("off");
  const off = await view();
  assert.equal(off.controls, false);
  assert.equal(off.footer, false);
  assert.deepEqual(
    off.features.map((f) => f.properties.kind),
    ["route"],
  );
  assert.deepEqual(off.layers, [true, true, true]);

  await mode("one_way");
  holdRun = true;
  await evaluate(`document.getElementById('run-generate').click()`);
  await until(`document.getElementById('run-generate').getAttribute('aria-busy') === 'true'`);
  for (let i = 0; i < 100 && !heldRun; i++) await new Promise((r) => setTimeout(r, 10));
  assert.ok(heldRun);
  await mode("round_trip");
  await answerRun(heldRun).catch(() => {}); // Chrome may have already dropped the aborted request.
  holdRun = false;
  assert.equal((await view()).results, false);
  runError = true;
  await evaluate(`document.getElementById('run-generate').click()`);
  await until(`document.getElementById('run-status').textContent.includes('Planner fixture busy')`);
  runError = false;
  await evaluate(`document.getElementById('run-clear').click()`);
  assert.equal((await view()).results, false);
  assert.equal(
    await evaluate(`document.getElementById('run-home-value').textContent`),
    "Choose on map",
  );
  assert.deepEqual((await view()).layers, [true, true, true]);

  await call("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await mode("one_way");
  assert.equal((await view()).shape, false);
  assert.equal(
    await evaluate(
      `document.getElementById('plan-mode').getBoundingClientRect().right <= innerWidth`,
    ),
    true,
  );
  await screenshot("one-way-mobile");
  await evaluate(`for (const [lng, lat] of [[24.795, 60.216], [24.815, 60.225]]) {
    window.__map.fire('click', {lngLat: {lng, lat}, point: window.__map.project([lng, lat]), originalEvent: new MouseEvent('click')});
  }; void 0;`);
  await until(`!document.getElementById('run-generate').disabled`);
  await evaluate(`document.getElementById('run-generate').click()`);
  await until(`document.getElementById('run-candidates').textContent.includes('1.20')`);
  await evaluate(`document.getElementById('run-elevation').click()`);
  await until(
    `!!document.querySelector('#route-profile-content svg') && !document.getElementById('route-inspection').hidden`,
  );
  assert.equal(
    await evaluate(
      `document.getElementById('route-inspection').getBoundingClientRect().right <= innerWidth`,
    ),
    true,
  );
  await screenshot("route-elevation-mobile");
  await mode("round_trip");
  assert.equal(await evaluate(`document.getElementById('route-inspection').hidden`), true);
  assert.equal((await view()).shape, true);
  await screenshot("round-trip-mobile");
  assert.deepEqual(exceptions, []);
  fs.writeFileSync(
    path.join(directory, "observations.json"),
    JSON.stringify({ requests, final: await view() }, null, 2),
  );
  console.log(
    "Planner browser checks passed: route modes, independent layers, Off, orientation, cancellation, failures, Clear, and mobile layout.",
  );
} finally {
  await call("Fetch.disable");
  socket.close();
}

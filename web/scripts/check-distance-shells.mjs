/** Exercise the legacy distance-shell browser flow and record rendering observations. */
// Requires Vite on 5173, the API on 8000, and headless Chrome's debugger on 9231.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const directory = process.argv[2] ?? "../artifacts/distance-shells/browser";
fs.mkdirSync(directory, { recursive: true });
const tabs = await (await fetch("http://127.0.0.1:9231/json")).json();
const socket = new WebSocket(tabs.find((tab) => tab.type === "page").webSocketDebuggerUrl);
await new Promise((resolve) => socket.addEventListener("open", resolve));
let sequence = 0;
const pending = new Map();
const requests = [];
const exceptions = [];
let hideShellCapability = false;
function call(method, params = {}) {
  const id = ++sequence;
  const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  socket.send(JSON.stringify({ id, method, params }));
  return result;
}
socket.addEventListener("message", async (event) => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const task = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) task.reject(message.error);
    else task.resolve(message.result);
  } else if (message.method === "Network.requestWillBeSent") {
    requests.push(message.params.request.url);
  } else if (message.method === "Runtime.exceptionThrown") {
    exceptions.push(message.params.exceptionDetails);
  } else if (message.method === "Fetch.requestPaused") {
    const { requestId } = message.params;
    const response = await call("Fetch.getResponseBody", { requestId });
    let body = response.base64Encoded
      ? Buffer.from(response.body, "base64").toString()
      : response.body;
    const configuration = message.params.request.url.includes("/api/paths/config");
    if (configuration) {
      const config = JSON.parse(body);
      if (hideShellCapability) config.shell_methods = [];
      body = JSON.stringify(config);
    } else body += '\nObject.defineProperty(window, "__map", {get: () => map});';
    await call("Fetch.fulfillRequest", {
      requestId,
      responseCode: 200,
      responseHeaders: [
        {
          name: "Content-Type",
          value: configuration ? "application/json" : "application/javascript",
        },
      ],
      body: Buffer.from(body).toString("base64"),
    });
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
  return evaluate(`new Promise((resolve, reject) => {
    let attempts = 0;
    const timer = setInterval(() => {
      if (${expression}) { clearInterval(timer); resolve(true); }
      else if (++attempts > 300) { clearInterval(timer); reject(Error('Timed out: ' + ${JSON.stringify(expression)})); }
    }, 100);
  })`);
}
async function screenshot(name) {
  const result = await call("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(path.join(directory, name + ".png"), Buffer.from(result.data, "base64"));
}
const dataExpression = `window.__map.getSource('distance-shells').serialize().data`;
const observations = {};
try {
  await call("Page.enable");
  await call("Runtime.enable");
  await call("Network.enable");
  await call("Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await call("Fetch.enable", {
    patterns: [
      { urlPattern: "*/src/main.ts*", requestStage: "Response" },
      { urlPattern: "*/api/paths/config", requestStage: "Response" },
    ],
  });
  await call("Page.navigate", { url: "http://127.0.0.1:5173/" });
  await until(`window.__map && !document.getElementById('mode-distance').disabled`);
  requests.length = 0;
  await evaluate(`document.getElementById('mode-distance').click();
    document.getElementById('outing-budget').value = '5';
    document.getElementById('outing-budget').dispatchEvent(new Event('change'));
    window.__map.jumpTo({center:[24.795,60.216],zoom:12});
    window.__map.fire('click', {lngLat:{lng:24.795,lat:60.216}, point:window.__map.project([24.795,60.216]), originalEvent:{target:window.__map.getCanvas()}}); void 0;`);
  await until(
    `document.getElementById('path-status').dataset.state === 'ready' && ${dataExpression}.features.length === 10`,
  );
  observations.grid =
    await evaluate(`({status:document.getElementById('distance-shell-status').textContent,
    rendered:window.__map.queryRenderedFeatures({layers:['distance-shell-fill']}).length,
    pathVisibility:window.__map.getLayoutProperty('path-grades','visibility')})`);
  assert.equal(observations.grid.pathVisibility, "none");
  assert.ok(observations.grid.rendered > 0);
  await screenshot("grid-5km");
  const snapshotRequests = requests.filter((url) => url.endsWith("/api/paths/snapshots")).length;
  await evaluate(`document.getElementById('distance-shell-method').value='triangulation';
    document.getElementById('distance-shell-method').dispatchEvent(new Event('change'));`);
  await until(
    `document.getElementById('distance-shell-status').textContent.startsWith('Filtered triangulation') && document.getElementById('path-status').dataset.state === 'ready'`,
  );
  observations.triangulation =
    await evaluate(`({status:document.getElementById('distance-shell-status').textContent,
    rendered:window.__map.queryRenderedFeatures({layers:['distance-shell-fill']}).length})`);
  assert.ok(observations.triangulation.rendered > 0);
  assert.equal(
    requests.filter((url) => url.endsWith("/api/paths/snapshots")).length,
    snapshotRequests,
  );
  assert.equal(requests.filter((url) => url.includes("/api/paths/tiles/")).length, 0);
  await screenshot("triangulation-5km");
  await evaluate(
    `window.__map.fire('click', {lngLat:{lng:24.795,lat:60.216}, point:window.__map.project([24.795,60.216]), originalEvent:{target:window.__map.getCanvas()}}); void 0;`,
  );
  await until(
    `!document.getElementById('path-inspection').hidden && window.__map.getSource('path-selection').serialize().data.features.length > 0`,
  );
  observations.inspection = true;
  await evaluate(`document.getElementById('distance-shell-method').value='grid';
    document.getElementById('distance-shell-method').dispatchEvent(new Event('change'));
    document.getElementById('outing-budget').value='1';
    document.getElementById('outing-budget').dispatchEvent(new Event('change'));
    document.getElementById('outing-budget').value='2';
    document.getElementById('outing-budget').dispatchEvent(new Event('change'));`);
  await until(
    `document.getElementById('path-status').dataset.state === 'ready' && ${dataExpression}.features.length === 4 && ${dataExpression}.features[3].properties.upper_m === 2000`,
  );
  observations.staleRequestsDiscarded = true;
  await evaluate(`document.getElementById('outing-clear-start').click()`);
  await until(
    `${dataExpression}.features.length === 0 && document.getElementById('path-status').dataset.state === 'choose_start'`,
  );
  observations.clearStart = true;
  await evaluate(`window.__map.jumpTo({center:[24.795,60.216],zoom:15});
    document.getElementById('mode-gradient').click();`);
  assert.equal(
    await evaluate(`window.__map.getLayoutProperty('distance-shell-fill','visibility')`),
    "none",
  );
  assert.equal(
    await evaluate(`window.__map.getLayoutProperty('path-grades','visibility')`),
    "visible",
  );
  observations.gradientRestored = true;
  await until(`document.getElementById('path-status').dataset.state === 'ready'
    && window.__map.isSourceLoaded(window.__map.getLayer('path-grades').source)`);
  assert.ok(
    await evaluate(`window.__map.queryRenderedFeatures({layers:['path-grades']}).length > 0`),
  );
  hideShellCapability = true;
  await call("Page.navigate", { url: "http://127.0.0.1:5173/" });
  await until(`window.__map && !document.getElementById('mode-gradient').disabled`);
  assert.equal(await evaluate(`document.getElementById('mode-distance').disabled`), true);
  assert.equal(await evaluate(`document.getElementById('distance-shell-method').disabled`), true);
  observations.distanceDisabledWithoutNumericData = true;
  assert.deepEqual(exceptions, []);
  fs.writeFileSync(
    path.join(directory, "results.json"),
    JSON.stringify({ observations, requests, exceptions }, null, 2),
  );
  console.log(JSON.stringify(observations, null, 2));
} catch (error) {
  fs.writeFileSync(
    path.join(directory, "failure.json"),
    JSON.stringify({ observations, requests, exceptions, error: String(error) }, null, 2),
  );
  console.error(
    await evaluate(
      `({path:document.getElementById('path-status')?.textContent,shell:document.getElementById('distance-shell-status')?.textContent})`,
    ),
  );
  await screenshot("failure").catch(console.error);
  throw error;
} finally {
  socket.close();
}

// Run with Chrome's remote debugger on 9225 and Vite on 5173.
import fs from 'node:fs';
import path from 'node:path';
const outputPath = process.argv[2] || 'data/experiments/path-tiles-browser/benchmark.json';
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
const tabs = await (await fetch('http://127.0.0.1:9225/json')).json();
const socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
await new Promise(resolve => socket.addEventListener('open', resolve));
let sequence = 0;
const pending = new Map();
function call(method, params = {}) {
  const id = ++sequence;
  const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  socket.send(JSON.stringify({ id, method, params }));
  return result;
}
socket.addEventListener('message', async event => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const task = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) task.reject(message.error);
    else task.resolve(message.result);
  } else if (message.method === 'Fetch.requestPaused') {
    const { requestId } = message.params;
    const response = await call('Fetch.getResponseBody', { requestId });
    const body = (response.base64Encoded ? Buffer.from(response.body, 'base64').toString() : response.body)
      + '\nObject.defineProperty(window, "__map", {get: () => map});';
    await call('Fetch.fulfillRequest', { requestId, responseCode: 200,
      responseHeaders: [{ name: 'Content-Type', value: 'application/javascript' }],
      body: Buffer.from(body).toString('base64') });
  }
});
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
try {
  await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await call('Fetch.enable', { patterns: [{ urlPattern: '*/src/main.ts*', requestStage: 'Response' }] });
  await call('Page.navigate', { url: 'http://127.0.0.1:5173/' });
  await evaluate(`new Promise((resolve, reject) => {
    let attempts = 0;
    const timer = setInterval(() => {
      if (window.__map && document.getElementById('path-status').dataset.state === 'ready') {
        clearInterval(timer); resolve();
      } else if (++attempts > 200) { clearInterval(timer); reject(Error('Map startup timed out')); }
    }, 100);
  })`);
  const result = await evaluate(fs.readFileSync(new URL('./benchmark-path-tiles.js', import.meta.url), 'utf8'));
  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2) + '\n');
} catch (error) {
  const partial = await evaluate('window.__tileBenchResult');
  if (partial) fs.writeFileSync(outputPath, JSON.stringify({ ...partial, incomplete: true }, null, 2) + '\n');
  throw error;
} finally {
  socket.close();
}

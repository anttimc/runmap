/** Check shared settings and browser-local presets using Vite, the API, and headless Chrome. */
// Requires Chrome's debugging endpoint; RUNMAP_DEBUG_PORT defaults to 9233.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const port = process.env.RUNMAP_DEBUG_PORT ?? "9233";
const origin = process.env.RUNMAP_WEB_ORIGIN ?? "http://127.0.0.1:5173";
const directory = process.argv[2] ?? "/private/tmp/runmap-settings-check";
await mkdir(directory, { recursive: true });
const tab = await (
  await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })
).json();
const socket = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((resolve) => socket.addEventListener("open", resolve));
let sequence = 0;
const pending = new Map();
const errors = [];
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails);
  if (!message.id) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message));
  else resolve(message.result);
});
function call(method, params = {}) {
  const id = ++sequence;
  const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  socket.send(JSON.stringify({ id, method, params }));
  return result;
}
async function evaluate(expression) {
  const result = await call("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function wait(expression) {
  for (let attempt = 0; attempt < 120; attempt++) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out: ${expression}`);
}
async function screenshot(name) {
  const result = await call("Page.captureScreenshot");
  await writeFile(`${directory}/${name}.png`, Buffer.from(result.data, "base64"));
}
let original;
try {
  await call("Runtime.enable");
  await call("Page.enable");
  await call("Emulation.setDeviceMetricsOverride", {
    width: 1280,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await call("Page.navigate", { url: origin });
  await wait('document.getElementById("preset-controls")?.disabled === false');
  original = await evaluate('localStorage.getItem("runmap.preference-presets.v1")');
  assert.equal(await evaluate('document.querySelectorAll("#run-hills").length'), 1);
  await evaluate('document.querySelector("[data-run-settings]").click()');
  assert.equal(await evaluate('document.getElementById("run-settings-panel").hidden'), false);
  assert.equal(await evaluate('document.querySelector(".controls").dataset.view'), "settings");
  assert.equal(await evaluate('document.querySelectorAll("dialog:modal").length'), 0);
  assert.equal(await evaluate("document.activeElement.id"), "run-settings-back");
  assert.equal(
    await evaluate('document.querySelector(".controls").getBoundingClientRect().left'),
    18,
  );
  await evaluate(`
    document.getElementById("plan-mode").value = "one_way";
    document.getElementById("plan-mode").dispatchEvent(new Event("change"));
    document.getElementById("path-surface").value = "surfaced";
    document.getElementById("path-surface").dispatchEvent(new Event("change"));
    document.getElementById("run-hills").value = "1";
    document.getElementById("run-hills").dispatchEvent(new Event("input"));
    document.getElementById("preset-new").click();
    document.getElementById("preset-name").value = "Browser QA " + Date.now();
    document.getElementById("preset-name").dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true, cancelable: true}));
  `);
  assert.match(await evaluate('document.getElementById("preset-status").textContent'), /Saved/);
  assert.equal(await evaluate('document.getElementById("preset-create").hidden'), true);
  assert.equal(await evaluate('document.getElementById("preset-update").disabled'), true);
  assert.equal(await evaluate('document.getElementById("preset-load")'), null);
  const savedId = await evaluate('document.getElementById("run-preset").value');
  await screenshot("desktop");
  assert.equal(
    await evaluate(`(() => {
      const header = document.querySelector("#run-settings-panel > header");
      const content = document.querySelector("#run-settings-panel .settings-content");
      const top = header.getBoundingClientRect().top;
      content.scrollTop = content.scrollHeight;
      const fixed = content.scrollTop > 0 && header.getBoundingClientRect().top === top;
      content.scrollTop = 0;
      return fixed;
    })()`),
    true,
  );
  const previousDocument = await evaluate("performance.timeOrigin");
  await call("Page.reload");
  await wait(`performance.timeOrigin !== ${previousDocument}`);
  await wait('document.getElementById("preset-controls")?.disabled === false');
  await evaluate(`
    document.querySelector("[data-run-settings]").click();
    document.getElementById("run-preset").value = ${JSON.stringify(savedId)};
    document.getElementById("run-preset").dispatchEvent(new Event("change"));

  `);
  assert.equal(await evaluate('document.getElementById("path-surface").value'), "surfaced");
  assert.equal(await evaluate('document.getElementById("run-hills").value'), "1");
  assert.equal(await evaluate('document.getElementById("run-hills-value").textContent'), "Low");
  await evaluate(`
    for (const mode of ["round_trip", "one_way"]) {
      document.getElementById("plan-mode").value = mode;
      document.getElementById("plan-mode").dispatchEvent(new Event("change"));
    }
  `);
  assert.equal(await evaluate('document.getElementById("run-hills").value'), "1");
  assert.equal(await evaluate('document.getElementById("run-shape-controls").hidden'), true);
  await evaluate('document.getElementById("run-settings-back").focus()');
  await call("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Tab",
    code: "Tab",
    windowsVirtualKeyCode: 9,
  });
  await call("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Tab",
    code: "Tab",
    windowsVirtualKeyCode: 9,
  });
  assert.equal(
    await evaluate(
      'document.querySelector("#run-settings-panel .help-content").matches(":popover-open")',
    ),
    true,
  );
  await call("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Escape",
    code: "Escape",
    windowsVirtualKeyCode: 27,
  });
  await call("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Escape",
    code: "Escape",
    windowsVirtualKeyCode: 27,
  });
  // The first Escape dismisses help, leaving settings available.
  assert.equal(await evaluate('document.getElementById("run-settings-panel").hidden'), false);
  await evaluate('document.getElementById("run-settings-back").click()');
  assert.equal(await evaluate('document.getElementById("run-settings-panel").hidden'), true);
  assert.equal(await evaluate('document.activeElement.hasAttribute("data-run-settings")'), true);
  await evaluate('document.querySelector("[data-run-settings]").click()');
  await call("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Escape",
    code: "Escape",
    windowsVirtualKeyCode: 27,
  });
  await call("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape" });
  await wait('document.getElementById("run-settings-panel").hidden === true');
  await call("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await evaluate('document.querySelector("[data-run-settings]").click()');
  assert.equal(
    await evaluate(`(() => {
    const panel = document.getElementById("run-settings-panel");
    const bounds = panel.getBoundingClientRect();
    return bounds.left >= 0 && bounds.right <= innerWidth && panel.scrollWidth <= panel.clientWidth;
  })()`),
    true,
  );
  await screenshot("mobile");
  await evaluate('document.getElementById("preset-new").click()');
  assert.equal(await evaluate("document.activeElement.id"), "preset-name");
  await screenshot("mobile-new-preset");
  await call("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Escape",
    code: "Escape",
    windowsVirtualKeyCode: 27,
  });
  await call("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Escape",
    code: "Escape",
    windowsVirtualKeyCode: 27,
  });
  assert.equal(await evaluate('document.getElementById("preset-create").hidden'), true);
  assert.equal(await evaluate('document.getElementById("run-settings-panel").hidden'), false);
  await evaluate('document.getElementById("preset-new").click()');
  await evaluate('document.getElementById("preset-cancel").click()');
  assert.equal(await evaluate('document.getElementById("preset-create").hidden'), true);
  await evaluate(`
    document.getElementById("run-hills").value = "2";
    document.getElementById("run-hills").dispatchEvent(new Event("input", {bubbles: true}));
  `);
  assert.equal(await evaluate('document.getElementById("preset-update").disabled'), false);
  assert.equal(
    await evaluate('document.getElementById("preset-status").textContent'),
    "Unsaved changes",
  );
  await evaluate('document.getElementById("preset-update").click()');
  assert.equal(await evaluate('document.getElementById("preset-create").hidden'), true);
  assert.equal(await evaluate('document.getElementById("preset-update").disabled'), true);
  await evaluate('document.getElementById("preset-delete").click()');
  assert.match(await evaluate('document.getElementById("preset-status").textContent'), /Deleted/);
  await evaluate(`
    document.getElementById("plan-mode").value = "round_trip";
    document.getElementById("plan-mode").dispatchEvent(new Event("change"));
  `);
  assert.equal(await evaluate('document.getElementById("run-shape-controls").hidden'), false);
  await screenshot("mobile-round-trip");
  await call("Emulation.setDeviceMetricsOverride", {
    width: 320,
    height: 568,
    deviceScaleFactor: 1,
    mobile: true,
  });
  assert.equal(
    await evaluate(
      'document.getElementById("run-settings-panel").scrollWidth <= document.getElementById("run-settings-panel").clientWidth',
    ),
    true,
  );
  await screenshot("mobile-compact");
  assert.deepEqual(errors, []);
  console.log(`Settings browser checks passed; screenshots: ${directory}`);
} finally {
  if (original !== undefined) {
    await evaluate(
      original === null
        ? 'localStorage.removeItem("runmap.preference-presets.v1")'
        : `localStorage.setItem("runmap.preference-presets.v1", ${JSON.stringify(original)})`,
    );
  }
  socket.close();
  await fetch(`http://127.0.0.1:${port}/json/close/${tab.id}`);
}

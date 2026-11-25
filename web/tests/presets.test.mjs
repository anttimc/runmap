/** Saved preferences survive reloads, reject damaged data, and respect current capabilities. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { decodePresets, encodePresets, preferenceIds, PRESET_STORAGE_KEY } from "../src/presets.ts";
import { startRunSettings } from "../src/run-settings.ts";

function settings() {
  return {
    preferences: Object.fromEntries(preferenceIds.map((id) => [id, "low"])),
    surface: "surfaced",
    avoidStairs: true,
    snapRadius: 75,
    loopWidth: 0.3,
    orientation: "counter-clockwise",
  };
}

test("versioned presets round-trip semantic levels and reject invalid or duplicate records", () => {
  const preset = { id: "one", name: "Gentle paths", settings: settings() };
  assert.deepEqual(decodePresets(encodePresets([preset])), [preset]);
  assert.deepEqual(decodePresets(null), []);
  for (const value of ["{", '{"version":2,"presets":[]}', '{"version":1,"presets":[null]}'])
    assert.throws(() => decodePresets(value));
  for (const patch of [
    { snapRadius: 0 },
    { snapRadius: 201 },
    { snapRadius: 1.5 },
    { loopWidth: 0.7 },
    { orientation: "other" },
    { preferences: {} },
  ])
    assert.throws(() => encodePresets([{ ...preset, settings: { ...settings(), ...patch } }]));
  assert.throws(() => encodePresets([preset, preset]));
  assert.throws(() => encodePresets([preset, { ...preset, id: "two", name: "GENTLE PATHS" }]));
});

function setup(context, initial = null) {
  class Control extends EventTarget {
    value = "";
    textContent = "";
    checked = false;
    disabled = false;
    dataset = {};
    replaceChildren(...children) {
      this.children = children;
      this.value = children[0]?.value ?? "";
    }
    reportValidity() {
      return true;
    }
    focus() {}
    setAttribute(key, value) {
      this[key] = value;
    }
  }
  const ids = [
    ...preferenceIds,
    "path-surface",
    "path-avoid-stairs",
    "outing-radius",
    "run-avoidance",
    "run-orientation",
    "preset-status",
    "run-preset",
    "preset-name",
    "preset-controls",
    "run-settings-panel",
    "preset-new",
    "preset-create",
    "preset-cancel",
    "preset-actions",
    "preset-update",
    "preset-delete",
    "preset-save",
  ];
  const nodes = Object.fromEntries(ids.map((id) => [id, new Control()]));
  for (const id of preferenceIds) {
    nodes[id].dataset.levels = '["off","low","high"]';
    nodes[id].value = "1";
  }
  nodes["path-surface"].value = "surfaced";
  nodes["path-avoid-stairs"].checked = true;
  nodes["outing-radius"].value = "75";
  nodes["run-avoidance"].value = "0.3";
  nodes["run-orientation"].value = "counter-clockwise";
  nodes["preset-controls"].disabled = true;
  nodes["preset-create"].hidden = true;
  nodes["preset-actions"].hidden = true;
  const stored = new Map(initial === null ? [] : [[PRESET_STORAGE_KEY, initial]]);
  const storage = {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
  };
  globalThis.document = { getElementById: (id) => nodes[id] };
  globalThis.window = { localStorage: storage };
  globalThis.Option = class {
    constructor(text, value) {
      this.text = text;
      this.value = value;
    }
  };
  context.after(() => {
    delete globalThis.document;
    delete globalThis.window;
    delete globalThis.Option;
  });
  return { nodes, stored, storage, click: (id) => nodes[id].dispatchEvent(new Event("click")) };
}

test("new naming, automatic selection, explicit updates, reload and deletion preserve shared preferences and endpoints stay outside presets", (context) => {
  const { nodes, stored, storage, click } = setup(context);
  let policyChanges = 0;
  startRunSettings(() => {
    policyChanges++;
    // Every value must already be restored when routing controllers are notified.
    assert.equal(nodes["outing-radius"].value, "75");
    assert.equal(nodes["run-avoidance"].value, "0.3");
  });
  assert.equal(nodes["preset-create"].hidden, true);
  click("preset-new");
  assert.equal(nodes["preset-create"].hidden, false);
  nodes["preset-name"].value = "Gentle paths";
  click("preset-save");
  assert.equal(nodes["preset-create"].hidden, true);
  assert.equal(nodes["preset-update"].disabled, true);
  const saved = decodePresets(stored.get(PRESET_STORAGE_KEY));
  assert.deepEqual(saved[0].settings, settings());
  nodes["outing-radius"].value = "10";
  nodes["run-avoidance"].value = "0";
  nodes["run-signals"].dataset.levels = '["off"]';
  nodes["run-signals"].disabled = true;
  nodes["run-preset"].dispatchEvent(new Event("change"));
  assert.equal(policyChanges, 1);
  assert.equal(nodes["run-signals"].value, "0");
  assert.match(nodes["preset-status"].textContent, /Unavailable preferences/);
  click("preset-new");
  nodes["preset-name"].value = "gentle PATHS";
  click("preset-save");
  assert.equal(decodePresets(stored.get(PRESET_STORAGE_KEY)).length, 1);
  assert.equal(nodes["preset-create"].hidden, false);
  click("preset-cancel");
  assert.equal(nodes["preset-create"].hidden, true);
  nodes["run-hills"].value = "2";
  nodes["run-hills"].dispatchEvent(new Event("input"));
  assert.equal(nodes["preset-update"].disabled, false);
  assert.match(nodes["preset-status"].textContent, /Unsaved changes/);
  assert.equal(
    decodePresets(stored.get(PRESET_STORAGE_KEY))[0].settings.preferences["run-hills"],
    "low",
  );
  click("preset-update");
  assert.equal(
    decodePresets(stored.get(PRESET_STORAGE_KEY))[0].settings.preferences["run-hills"],
    "high",
  );
  storage.setItem = () => {
    throw new Error("quota");
  };
  click("preset-delete");
  assert.match(nodes["preset-status"].textContent, /Could not save/);
  assert.equal(decodePresets(stored.get(PRESET_STORAGE_KEY)).length, 1);
  storage.setItem = (key, value) => stored.set(key, value);
  click("preset-delete");
  assert.deepEqual(decodePresets(stored.get(PRESET_STORAGE_KEY)), []);
  // A new controller instance reads presets written during an earlier visit.
  stored.set(PRESET_STORAGE_KEY, encodePresets(saved));
  startRunSettings(() => {});
  assert.equal(nodes["run-preset"].children[1].text, "Gentle paths");
});

test("damaged storage leaves settings usable and does not overwrite saved data", (context) => {
  const { nodes, stored } = setup(context, "damaged");
  startRunSettings(() => {});
  assert.equal(nodes["preset-controls"].disabled, true);
  assert.match(nodes["preset-status"].textContent, /could not be read/);
  assert.equal(stored.get(PRESET_STORAGE_KEY), "damaged");
  assert.equal(nodes["run-hills"].disabled, false);
});

test("new preset naming supports Enter, Escape and retry after a storage failure", (context) => {
  const { nodes, stored, storage, click } = setup(context);
  startRunSettings(() => {});
  const key = (value) =>
    nodes["preset-name"].dispatchEvent(
      Object.assign(new Event("keydown", { cancelable: true }), { key: value }),
    );
  click("preset-new");
  nodes["preset-name"].value = "Cancelled";
  key("Escape");
  assert.equal(nodes["preset-create"].hidden, true);
  assert.equal(stored.size, 0);
  click("preset-new");
  assert.equal(nodes["preset-name"].value, "");
  nodes["preset-name"].value = "Quiet paths";
  const setItem = storage.setItem;
  storage.setItem = () => {
    throw new Error("quota");
  };
  key("Enter");
  assert.equal(nodes["preset-create"].hidden, false);
  assert.equal(stored.size, 0);
  storage.setItem = setItem;
  key("Enter");
  assert.equal(nodes["preset-create"].hidden, true);
  assert.equal(decodePresets(stored.get(PRESET_STORAGE_KEY))[0].name, "Quiet paths");
  nodes["run-avoidance"].value = "0.4";
  nodes["run-settings-panel"].dispatchEvent(new Event("runsettingsopen"));
  assert.equal(nodes["preset-update"].disabled, false);
});

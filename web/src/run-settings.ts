/** Connect shared routing controls to named presets stored only in this browser. */
import { element } from "./dom.ts";
import {
  decodePresets,
  encodePresets,
  isPreferenceLevel,
  sameSettings,
  preferenceIds,
  PRESET_STORAGE_KEY,
  type PreferencePreset,
  type RunSettings,
} from "./presets.ts";

type Control = HTMLInputElement | HTMLSelectElement;
const control = (id: string): Control => element<Control>(id);

function levels(id: string): string[] {
  const value: unknown = JSON.parse(control(id).dataset.levels ?? "[]");
  return Array.isArray(value)
    ? value.filter((level): level is string => typeof level === "string")
    : [];
}
function readSettings(validate = true): RunSettings {
  const preferences = {} as RunSettings["preferences"];
  for (const id of preferenceIds) {
    const level = levels(id)[Number(control(id).value)];
    if (!isPreferenceLevel(level)) throw new Error("Routing preferences are not ready.");
    preferences[id] = level;
  }
  for (const id of ["outing-radius", "run-avoidance"]) {
    if (validate && !element<HTMLInputElement>(id).reportValidity())
      throw new Error("Check the settings values.");
  }
  return {
    preferences,
    surface: control("path-surface").value === "surfaced" ? "surfaced" : "all",
    avoidStairs: element<HTMLInputElement>("path-avoid-stairs").checked,
    snapRadius: Number(control("outing-radius").value),
    loopWidth: Number(control("run-avoidance").value),
    orientation:
      control("run-orientation").value === "clockwise" ? "clockwise" : "counter-clockwise",
  };
}

/** Apply all values before notifying controllers so requests see a complete settings set. */
function applySettings(settings: RunSettings, policyChanged: () => void): boolean {
  let adjusted = false;
  for (const id of preferenceIds) {
    const available = levels(id);
    const index = available.indexOf(settings.preferences[id]);
    adjusted ||= index < 0;
    control(id).value = String(index < 0 ? Math.max(0, available.indexOf("off")) : index);
  }
  control("path-surface").value = settings.surface;
  element<HTMLInputElement>("path-avoid-stairs").checked = settings.avoidStairs;
  control("outing-radius").value = String(settings.snapRadius);
  control("run-avoidance").value = String(settings.loopWidth);
  control("run-orientation").value = settings.orientation;
  policyChanged();
  for (const id of [...preferenceIds, "run-avoidance"])
    control(id).dispatchEvent(new Event("input", { bubbles: true }));
  control("run-orientation").dispatchEvent(new Event("change", { bubbles: true }));
  return adjusted;
}

export function startRunSettings(policyChanged: () => void): void {
  const status = element("preset-status");
  const select = element<HTMLSelectElement>("run-preset");
  const name = element<HTMLInputElement>("preset-name");
  let presets: PreferencePreset[];
  let storage: Storage;
  try {
    storage = window.localStorage;
    presets = decodePresets(storage.getItem(PRESET_STORAGE_KEY));
  } catch {
    status.textContent = "Saved presets could not be read. Run settings remain available.";
    return;
  }
  element<HTMLFieldSetElement>("preset-controls").disabled = false;
  const selected = (): PreferencePreset | undefined =>
    presets.find((preset) => preset.id === select.value);
  const create = element("preset-create");
  const newButton = element<HTMLButtonElement>("preset-new");
  function updateButtons(): void {
    const preset = selected();
    element("preset-actions").hidden = !preset || !create.hidden;
    let changed = false;
    try {
      changed = !!preset && !sameSettings(readSettings(false), preset.settings);
    } catch {
      /* Keep saving disabled until preferences are ready. */
    }
    element<HTMLButtonElement>("preset-update").disabled = !changed;
  }
  function creating(open: boolean): void {
    create.hidden = !open;
    newButton.hidden = open;
    newButton.setAttribute("aria-expanded", String(open));
    updateButtons();
    if (open) {
      name.value = "";
      status.textContent = "";
      name.focus();
    }
  }
  function render(id = ""): void {
    select.replaceChildren(
      new Option("Custom settings", ""),
      ...presets.map((preset) => new Option(preset.name, preset.id)),
    );
    select.value = id;
    updateButtons();
  }
  function persist(next: PreferencePreset[], id: string, message: string): boolean {
    try {
      storage.setItem(PRESET_STORAGE_KEY, encodePresets(next));
      presets = next;
      render(id);
      status.textContent = message;
      return true;
    } catch {
      status.textContent = "Could not save presets. Browser storage may be unavailable or full.";
      return false;
    }
  }
  function save(update: boolean): void {
    try {
      const current = update ? selected() : undefined;
      const label = update ? current?.name : name.value.trim();
      if (!label) {
        name.focus();
        status.textContent = "Enter a preset name.";
        return;
      }
      if (
        !update &&
        presets.some((preset) => preset.name.toLocaleLowerCase() === label.toLocaleLowerCase())
      ) {
        status.textContent = "That name already exists. Choose another name.";
        name.focus();
        return;
      }
      const preset = {
        id: current?.id ?? crypto.randomUUID(),
        name: label,
        settings: readSettings(),
      };
      const next = update
        ? presets.map((item) => (item.id === preset.id ? preset : item))
        : [...presets, preset];
      if (persist(next, preset.id, `Saved “${label}”.`)) {
        creating(false);
        select.focus();
      }
    } catch (error: unknown) {
      status.textContent = error instanceof Error ? error.message : "Could not save preset.";
    }
  }
  select.addEventListener("change", () => {
    creating(false);
    const preset = selected();
    if (!preset) {
      status.textContent = "";
      return;
    }
    const adjusted = applySettings(preset.settings, policyChanged);
    updateButtons();
    status.textContent = adjusted ? "Unavailable preferences were set to Off." : "";
  });
  function settingsEdited(): void {
    updateButtons();
    status.textContent =
      selected() && !element<HTMLButtonElement>("preset-update").disabled ? "Unsaved changes" : "";
  }
  for (const id of [
    ...preferenceIds,
    "run-avoidance",
    "run-orientation",
    "path-surface",
    "path-avoid-stairs",
    "outing-radius",
  ]) {
    for (const event of ["input", "change"]) control(id).addEventListener(event, settingsEdited);
  }
  element("run-settings-panel").addEventListener("runsettingsopen", settingsEdited);
  newButton.addEventListener("click", () => creating(true));
  element("preset-cancel").addEventListener("click", () => {
    creating(false);
    status.textContent = "";
    newButton.focus();
  });
  name.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      save(false);
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      creating(false);
      status.textContent = "";
      newButton.focus();
    }
  });
  element("preset-save").addEventListener("click", () => save(false));
  element("preset-update").addEventListener("click", () => save(true));
  element("preset-delete").addEventListener("click", () => {
    const preset = selected();
    if (preset)
      persist(
        presets.filter((item) => item.id !== preset.id),
        "",
        `Deleted “${preset.name}”.`,
      );
  });
  render();
}

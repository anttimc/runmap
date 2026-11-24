/** Validate versioned browser presets independently of available routing data. */
export const preferenceIds = [
  "run-hills",
  "run-signals",
  "run-environment_greenery",
  "run-environment_water",
  "run-environment_traffic",
  "run-environment_wetland",
] as const;
export type PreferenceId = (typeof preferenceIds)[number];
export type PreferenceLevel = "off" | "low" | "high";
export interface RunSettings {
  preferences: Record<PreferenceId, PreferenceLevel>;
  surface: "all" | "surfaced";
  avoidStairs: boolean;
  snapRadius: number;
  loopWidth: number;
  orientation: "clockwise" | "counter-clockwise";
}
export interface PreferencePreset {
  id: string;
  name: string;
  settings: RunSettings;
}
/** Compare values independently of JSON property order. */
export function sameSettings(left: RunSettings, right: RunSettings): boolean {
  return (
    preferenceIds.every((id) => left.preferences[id] === right.preferences[id]) &&
    left.surface === right.surface &&
    left.avoidStairs === right.avoidStairs &&
    left.snapRadius === right.snapRadius &&
    left.loopWidth === right.loopWidth &&
    left.orientation === right.orientation
  );
}
export const PRESET_STORAGE_KEY = "runmap.preference-presets.v1";

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
export function isPreferenceLevel(value: unknown): value is PreferenceLevel {
  return value === "off" || value === "low" || value === "high";
}
function bounded(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
}
function validSettings(value: unknown): value is RunSettings {
  if (!record(value) || !record(value.preferences)) return false;
  const preferences = value.preferences;
  return (
    preferenceIds.every((id) => isPreferenceLevel(preferences[id])) &&
    (value.surface === "all" || value.surface === "surfaced") &&
    typeof value.avoidStairs === "boolean" &&
    bounded(value.snapRadius, 1, 200) &&
    Number.isInteger(value.snapRadius) &&
    bounded(value.loopWidth, 0, 0.6) &&
    (value.orientation === "clockwise" || value.orientation === "counter-clockwise")
  );
}

/** Reject damaged or future formats rather than silently overwriting saved preferences. */
export function decodePresets(serialized: string | null): PreferencePreset[] {
  if (serialized === null) return [];
  const value: unknown = JSON.parse(serialized);
  if (!record(value) || value.version !== 1 || !Array.isArray(value.presets))
    throw new Error("Unsupported preset format");
  const ids = new Set<string>();
  const names = new Set<string>();
  return value.presets.map((preset: unknown) => {
    if (
      !record(preset) ||
      typeof preset.id !== "string" ||
      !preset.id ||
      typeof preset.name !== "string" ||
      !preset.name.trim() ||
      preset.name.length > 60 ||
      ids.has(preset.id) ||
      names.has(preset.name.trim().toLocaleLowerCase()) ||
      !validSettings(preset.settings)
    )
      throw new Error("Invalid preset");
    ids.add(preset.id);
    names.add(preset.name.trim().toLocaleLowerCase());
    return { id: preset.id, name: preset.name.trim(), settings: preset.settings };
  });
}
export function encodePresets(presets: PreferencePreset[]): string {
  const serialized = JSON.stringify({ version: 1, presets });
  decodePresets(serialized);
  return serialized;
}

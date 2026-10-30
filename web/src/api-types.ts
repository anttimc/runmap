/** HTTP payload types shared by map rendering and feature controllers. */
import type { FeatureCollection, MultiPolygon, Polygon } from "geojson";

export type Location = [number, number];
export type PreferenceLevel = "off" | "low" | "high";
export interface ColorStop {
  percent: number;
  color: string;
}
export interface PathPolicy {
  surface: "all" | "surfaced";
  avoid_stairs: boolean;
  radius_m: number;
}

export interface MapConfig {
  bounds_wgs84: [number, number, number, number];
  image_coordinates: [[number, number], [number, number], [number, number], [number, number]];
  source_shape: [number, number];
  resolution_m: number;
  smoothing: {
    minimum: number;
    maximum: number;
    step: number;
    default: number;
    raw_value: number;
  };
  slope_legend: { percent: number; color: string; opacity: number }[];
  terrain_tiles?: {
    template: string;
    tile_size: number;
    min_zoom: number;
    max_zoom: number;
  };
  contour_tiles?: {
    template: string;
    tile_size: number;
    min_zoom: number;
    max_zoom: number;
    interval_m: number;
    index_interval_m: number;
  };
}

export interface PathConfig {
  run_planner?: RunPlannerConfiguration;
  status: "ready";
  dataset_id: string;
  coverage: { geometry: Polygon; kind: string };
  bounds_wgs84: [number, number, number, number];
  method: { wavelength_m: number; sampling_interval_m: number };
  min_zoom: number;
  tile_template: string;
  tile_min_zoom: number;
  tile_max_zoom: number;
  shell_methods?: ShellMethod[];
  snap_radius_m: number;
  default_budget_km: number;
  importer_notes: string[];
  initial_view: { longitude: number; latitude: number; zoom: number };
}
export interface Snap {
  segment_id: string;
  graph_offset_m: number;
  longitude: number;
  latitude: number;
  offset_m: number;
  description?: string;
}
export interface StartResponse {
  status: string;
  requested: [number, number];
  snap: Snap | null;
}
export interface SnapshotResponse {
  status: string;
  dataset_id: string;
  snapshot_id: string;
  expires_at: number;
  preparation_ms: number;
  start?: StartResponse;
  snap?: Snap | null;
  requested?: [number, number];
}

export type ShellMethod = "grid" | "triangulation";
export interface ShellResponse extends FeatureCollection<
  MultiPolygon,
  { lower_m: number; upper_m: number }
> {
  dataset_id: string;
  snapshot_id: string;
  method: ShellMethod;
  method_used: ShellMethod;
  subtract_straight_line: boolean;
  maximum_m: number;
  extra_travel_percentage: boolean;
  unit: "m" | "percent";
  maximum: number;
  interval: number;
  smoothing_m: number;
  smoothing_used_m: number;
  cell_m: number;
  interval_m: number;
  generation_ms: number;
  vertices: number;
}

export interface RunPlannerConfiguration {
  preference_defaults: { hills: PreferenceLevel };
  preference_capabilities: {
    hills: { supported_levels: PreferenceLevel[]; unavailable_reason: string | null };
  };
}
export interface TerrainObservations {
  estimated_ascent_m: number | null;
  estimated_descent_m: number | null;
  terrain_valid_fraction: number | null;
  terrain_trusted_fraction: number | null;
  terrain_fallback_distance_m: number | null;
}
export interface Leg {
  coordinates: Location[];
  distance_m: number;
  observations: TerrainObservations;
  traversals: Traversal[];
}
export interface Traversal {
  segment_id: string;
  start_m: number;
  end_m: number;
}
export type RunMode = "one_way" | "round_trip";
export interface Candidate {
  mode: RunMode;
  outbound_left: boolean | null;
  legs: Leg[];
  distance_m: number;
  repeated_distance_m: number;
  closest_centre_m: number | null;
  encloses_centre: boolean | null;
  equivalent_to: number | null;
}
export interface RunResponse {
  mode: RunMode;
  cost_model: { shape_settings: { avoidance_ratio: number } };
  dataset_id: string;
  status: string;
  home: Snap | null;
  turnaround: Snap | null;
  candidates: Candidate[];
  elapsed_s: number;
  coverage_note: string;
}

export interface ProfileInterval {
  start_m: number;
  end_m: number;
  start_elevation_m: number | null;
  end_elevation_m: number | null;
  grade_percent: number | null;
  coordinates: Location[];
  quality_flags: string[];
}
export interface RouteProfile {
  dataset_id: string;
  manifest_id: string | null;
  wavelength_m: number;
  sampling_interval_m: number;
  distance_m: number;
  turnaround_m: number | null;
  ascent_m: number;
  descent_m: number;
  valid_length_fraction: number;
  totals_complete: boolean;
  intervals: ProfileInterval[];
}

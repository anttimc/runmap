# 1. Project goals

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

## G0 — Understand terrain when choosing a run

Build an interactive map that helps a runner explore terrain before committing to a route.

### G1 — Find flatter running options

Support the user's aim of maintaining a steady pace with low effort by showing where hills occur and where flatter paths connect.

Success means the user can:

- See a broad overview of flatter and hillier areas.
- Identify relatively flat, connected running paths.
- Inspect short rises and sustained climbs that a total-ascent number could hide.
- Understand the direction in which a segment rises or falls when inspecting it.

Terrain is a route-selection input. This goal does not promise a particular heart rate or predict physiological response.

### G2 — Find hill-training opportunities

Show where useful climbs and substantial elevation changes occur.

Success means the user can:

- Locate potential training hills on the map.
- Inspect climb length, elevation gain, and gradient once their definitions are selected.
- Distinguish short steep climbs from long gentle climbs and repeated rolling hills.

### G3 — Learn by changing the implementation

Make the project understandable and practical for personal experimentation.

Success means the user can:

- Change a mathematical definition without rewriting data loading or the UI.
- Replace the elevation provider without rewriting the analysis.
- Run calculations outside the web application.
- Trace displayed results back to source data and processing settings.
- Optionally compare a Python calculation with an Odin or Go implementation.

### G4 — Explore interactively

Support panning and zooming, with an intended maximum viewport of approximately **20 km × 20 km**.

Success means:

- Navigation loads the appropriate visible data rather than repeatedly processing the entire elevation raster.
- Colour and threshold changes can update already loaded measurements.
- Expensive preparation is visibly separated from normal map interaction.
- Performance is measured on an agreed machine and study area before numerical latency targets are adopted.

The viewport size is not a limit on the total area the service may eventually cover.

## First release scope

Leppävaara, Espoo: a terrain map showing gradient magnitude and direction, selectable location details, a legend, and controls for comparing smoothing methods. Reduce noisy gradients while retaining meaningful hills, ridges, and slope transitions. See the [terrain experiment](06-leppavaara-terrain.md).

This is the first step toward G1 and G2. Terrain slope alone cannot establish the gradient or connectivity of a runnable path.

## Current product sequence

The terrain-map first release is the existing foundation. The active
[map-first plan](22-map-first-running-plan.md) now prioritizes:

1. An intuitive path-gradient map with inspection and start-based reachability.
2. Guided route assembly through selected places and retained sections.
3. Diverse automatic suggestions with terrain and geographic guidance.

The directed path graph supports every stage. Cycles are optional examples,
not a required partition or preprocessing product.

## Later possibilities

- Add runnable-path gradients and connected flat-path exploration.
- Summarize a manually selected or imported route.
- Generate routes with flatness or climbing preferences.
- Expand geographic coverage.
- Publish a public web service.
- Add a compiled implementation as a learning experiment.

## Outside the first release

Heart-rate prediction, training prescriptions, accounts, social features, live activity tracking, and turn-by-turn navigation.

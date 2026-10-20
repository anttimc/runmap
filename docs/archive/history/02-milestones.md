# 2. Delivery milestones

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

Status values: planned, active, complete. M0–M2 are complete, M3 is active,
M4 is active with M4.1–M4.4 implemented and M4.5 pending; later milestones are planned.
The [map-first plan](22-map-first-running-plan.md) defines the current product sequence. Completion requires the listed evidence, not
just implemented code.

## M0 — Define the first experiment

Supports: G1, G2, G3. Status: complete.

Tasks:

1. Study area selected: Leppävaara, Espoo. Choose exact bounds and confirm elevation coverage.
2. Initial approach selected: terrain elevation gradients with magnitude and direction visualization.
3. Compare raw derivatives, Gaussian smoothing, and regularized nonlinear diffusion; choose experiment parameters rather than a final winner.
4. Identify familiar flat ground, hills, ridges, and slope transitions for validation.
5. Refine the magnitude overlay, direction arrows, and raw/smoothed comparison interactions.

Complete when: area, experiment parameters, expected observations, and unresolved limitations are recorded. Formula choices may remain experimental, but the first comparison must be concrete.

## M1 — Create a reproducible local data pipeline

Supports: G3. Depends on: M0. Status: complete.

Tasks:

1. Establish the Python package and reproducible dependency setup.
2. Download a bounded elevation sample; record sources, dates, licences, and checksums where practical. OSM provides map context initially; network extraction comes later.
3. Inspect coordinate systems, coverage, nodata, and GeoTIFF layout.
4. Prepare efficient raster access, valid-data masks, and a processing buffer around the display area.
5. Save repeatable commands and processing metadata.

Complete when: a documented command produces inspectable local inputs for analysis without the web UI.

## M2 — Implement and validate terrain gradients and smoothing

Supports: G1, G2, G3. Depends on: M1. Status: complete.

Tasks:

1. Implement metric-aware raster derivatives and explicit uphill/downhill direction conventions.
2. Compare raw, Gaussian-smoothed, and regularized nonlinear-diffusion elevation surfaces.
3. Preserve nodata and handle boundaries and raster row orientation correctly.
4. Compare slope and direction errors on synthetic surfaces and inspect familiar locations.
5. Measure raster reads, smoothing, differentiation, and memory separately; cache numerical results.

Complete when: magnitude and direction are reproducible and validated, and the noise/feature-preservation tradeoff is documented. Known uncertainties are visible rather than silently classified as flat. See [experiment criteria](06-leppavaara-terrain.md).

## M3 — Deliver the first interactive map

Supports: G1, G2, G4. Depends on: M2. Status: active.

Tasks:

1. Build a MapLibre page with plain controls and a legend.
2. Display slope magnitude as a raster overlay and use elevation contours to
   communicate gradient direction, with selectable location details if sign is needed.
3. Add colour/threshold controls and any selected analysis parameters.
4. Provide FastAPI endpoints only for interactions requiring backend work.
5. Test panning and zooming up to the intended viewport size.
6. Use tiled delivery as data volume warrants; keep full-area source rasters out of browser responses.
7. Optimize cold overlay delivery: reproject directly from COGs, permit
   concurrent unique tile renders, move missing smoothing jobs out of request
   handling, simplify contours by zoom, and expand immutable tile caching in
   that order, measuring after each step.

Complete when: the user can inspect hill steepness and direction in Leppävaara and compare raw/smoothed results interactively. This establishes terrain exploration; runnable-path suitability is deferred.

## M4 — Explore runnable paths and start-based reachability

Supports: G1–G4. Depends on: M3. Status: active; M4.1–M4.4 implemented, M4.5 pending.

1. Display along-path gradient magnitude with direction on inspection.
2. Inspect sampled profiles, climbs, surfaces, access evidence, and uncertainty.
3. Select a start and show outward-and-return reachability under shared rules.
4. Add a minimum return-distance budget that explicitly permits retracing.
5. Validate familiar corridors, hills, bridges, restrictions, and extract edges;
   measure viewport and query performance.

Complete when: the runner can locate a promising path or hill, understand its
terrain, and see how it connects to their start. See the
[M4 implementation plan](23-m4-path-exploration.md) for delivery slices,
measurement definitions, interactions, and acceptance checks.
The gradient UI, shared eligibility, edge snapping, and budgeted interval
inclusion are implemented. Local fixture review and the performance gate remain.

## M7 — Guided route assembly

Supports: G1, G2, G4. Depends on: M4. Status: planned.

Select destinations and waypoints, compare return paths, inspect distance,
terrain, and repetition, and keep liked sections while revising the rest.
Validate full directed walks and explain infeasible requests.

The [smooth loop routing experiment](26-smooth-loop-routing-experiment.md)
defines a first algorithm experiment for tunable two-leg outings, supporting
M7/M8 without requiring globally optimal route pairs.

The [first routing UI plan](27-first-routing-ui.md) delivers endpoint selection,
two-candidate outings, avoidance tuning, and map diagnostics for user feedback.
It starts with distance costs on the prepared network; running-time costs follow.
The initial Explore / Plan run map tool is implemented and ready for feedback;
advanced tuning tools and broader local route review remain.

The accepted first feature set is followed by
[UI and settings improvements](28-planner-ui-and-settings.md): explicit tabs,
settings ownership, persistent planner actions, responsive layout, and compact
legends. A separate [routing preferences plan](29-routing-preferences.md) covers
hills, traffic lights, busy-road exposure, and green surroundings, with data
audits, soft costs, and observable tradeoffs. Deliver the UI checkpoint first;
enable preference controls as their routing support becomes available. The UI
checkpoint is implemented and checked on desktop and mobile viewport sizes;
routing preference contracts and [hill avoidance](32-hill-routing.md) are
implemented, with local runner acceptance pending. Other preference algorithms
remain planned. The
[existing-data audit](30-routing-preferences-audit.md) is recorded with local
fixtures; signal attribution, buffered environmental context, and local runner
validation remain open gates.

Complete when: a runner can assemble and revise a familiar outing and record
accepted/rejected examples for later route scoring.

## M8 — Automatic route suggestions

Supports: G1, G2, G4. Depends on: M7. Status: planned.

Extend the retained anchor baseline to generate a few diverse complete routes
with explicit user guidance and observable tradeoffs. Optional saved or
generated loops do not require a cycle basis or complete catalogue.

Complete when: suggestions satisfy stated constraints, provide meaningful
alternatives, and pass local-runner review against M7 examples.

M5 and M6 retain their existing identifiers below; their numbering does not
place them ahead of M7 or M8 in the product sequence.

## Optional milestones after the first release

### M5 — Public hosting

Prepare repeatable data builds, attribution, a basemap with suitable hosting terms, static map delivery, and an appropriate API deployment. Confirm the deployment scope before publishing.

### M6 — Compiled-language experiment

Choose a measured numerical bottleneck or explicit learning objective. Implement one bounded component in Odin or Go and compare outputs, execution time, and integration complexity against the Python reference. Adoption is optional even if the experiment succeeds.

# 2. Delivery milestones

Status values: planned, active, complete. M0–M2 are complete, M3 is active,
and later milestones are planned. Completion requires the listed evidence, not
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
2. Display slope magnitude as a raster overlay and direction as sparse arrows, with selectable location details.
3. Add colour/threshold controls and any selected analysis parameters.
4. Provide FastAPI endpoints only for interactions requiring backend work.
5. Test panning and zooming up to the intended viewport size.
6. Use tiled delivery as data volume warrants; keep full-area source rasters out of browser responses.

Complete when: the user can inspect hill steepness and direction in Leppävaara and compare raw/smoothed results interactively. This establishes terrain exploration; runnable-path suitability is deferred.

## M4 — Refine the tool against real running experience

Supports: G1–G4. Depends on: M3. Status: planned.

Tasks:

1. Compare map classifications with local observations.
2. Adjust analysis based on measured failures and the user's preferences.
3. Benchmark cold and cached interactions on the selected deployment class.
4. Refine zoom-level summaries and arrow density so short hills are not misleadingly hidden.
5. Document remaining limitations and prepare the next milestone for OSM path extraction and path-gradient analysis.

Complete when: the first release satisfies the goal criteria on the selected area and has documented performance and accuracy limitations.

## Optional milestones after the first release

### M5 — Public hosting

Prepare repeatable data builds, attribution, a basemap with suitable hosting terms, static map delivery, and an appropriate API deployment. Confirm the deployment scope before publishing.

### M6 — Compiled-language experiment

Choose a measured numerical bottleneck or explicit learning objective. Implement one bounded component in Odin or Go and compare outputs, execution time, and integration complexity against the Python reference. Adoption is optional even if the experiment succeeds.

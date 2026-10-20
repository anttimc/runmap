# Tiled path rendering

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

Status: implemented; see [measurements and operational limits](25-tiled-path-results.md). Follows [building-access measurements](24-building-access-results.md).

## Goal and accepted tradeoff

Replace viewport-wide path GeoJSON with zoom-dependent MVT vector tiles. Panning should reuse loaded tiles and avoid repeating exploration searches. A start-point change may take several seconds: show an explicit calculating state, prepare an exploration snapshot, then load tiles for that snapshot.

The current urban zoom-11 response contains about 195,000 features and 58 MB of JSON. Browser source processing alone takes about 0.75 s, with request and parsing taking longer. Tiling must reduce feature count and geometry detail as well as encoding size. MVT without generalization is insufficient.

Keep the original graph, intervals, legal routing, snapping, inspection, and building-access classification authoritative. Display generalization must not change their results.

## Architecture

Use the existing FastAPI service and MapLibre renderer. Start with lazily generated, cached MVT tiles; no PostGIS, separate tile server, or browser feature-state synchronization is required.

Two rendering contexts use the same tile pipeline:

- Static context: gradients and policy eligibility without a start. Versioned URLs can use long-lived public caching.
- Exploration snapshot: a fixed snapped start, routing policy, and budget, with prepared distances and interval inclusion. Tiles are generated against that immutable snapshot and reused during panning.

Snapshot preparation computes routing once and materializes interval state across the prepared dataset once. Include hidden intervals in that state so the display checkbox can toggle without rerunning routing. Use compact typed arrays or equivalent data-only containers rather than duplicating interval dictionaries. Record preparation time and retained memory separately. Grade values and original geometry remain shared with loaded path data.

Keep metric and visibility out of the routing snapshot identity. Gradient/distance changes and hiding building-access branches select tile variants using the same snapshot. Surface policy and stairs affect legal routing and therefore require a different snapshot. A budget change also requests a new snapshot; reuse existing bounded distance-search caches when valid.

## API and cache identity

Proposed API contract:

| Request | Purpose |
|---|---|
| `POST /api/paths/snapshots` | Validate dataset and options, snap start, prepare/reuse exploration snapshot |
| `GET /api/paths/tiles/v{version}/{dataset_id}/static/{policy}/{variant}/{z}/{x}/{y}.mvt` | Static gradient tile |
| `GET /api/paths/tiles/v{version}/{dataset_id}/snapshots/{snapshot_id}/{variant}/{z}/{x}/{y}.mvt` | Exploration tile |

Snapshot request accepts the existing longitude, latitude, radius, optional selected segment, surface, stairs, budget, and dataset identity. Return `ready` with snapshot ID, snap result, coverage metadata, tile template, expiry, and preparation timing, or the existing no-eligible-snap outcome. Use a synchronous POST initially; it can run in the service thread pool and the client can wait. Set a bounded preparation deadline and return a useful failure. Introduce a background job API only if measured preparation exceeds that practical deadline.

Canonical snapshot identity includes dataset digest, exploration algorithm version, policy, snapped segment and exact offset, and budget. Preserve exact snapping: do not round starts merely to increase cache hits. Requested-coordinate and offset-marker details belong to the response, not reusable snapshot state, because different clicks may share a snap.

Tile identity additionally includes render/generalization version, classifier version and limit, metric, building-access visibility, z/x/y, and any grade/distance quantization rules. A `variant` is a validated finite enum or canonical identifier, never an arbitrary expression.

Use bounded byte-size LRU caches for encoded tiles and snapshots, with configurable TTLs. Coalesce concurrent preparation for the same snapshot and concurrent requests for the same tile. Eviction must release distance/interval references. Start with a single API worker; in-memory snapshot IDs are process-local. Multiple workers require a shared snapshot store or deterministic reconstruction before deployment.

Static tiles use immutable versioned URLs and public caching. Exploration tiles use private browser caching with a lifetime no greater than snapshot lifetime. Return `410` for an expired snapshot; the frontend recreates it once, with retry protection. Empty tiles are successful valid MVT responses, not errors. Validate XYZ ranges and reject unknown datasets, versions, policies, and variants. Provide `ETag`, compute/cache timing headers, and explicit cache-hit metrics. Compression must be measured and configured at the HTTP layer, including `Vary: Accept-Encoding` where applicable.

## Tile generation and levels of detail

Use EPSG:3857 XYZ tiles, MVT extent 4096, and a consistent buffered clip region. Derive a pixel-sized buffer from maximum styled line width; start with 8 pixels and test seams. Transform and clip geometry before quantization; discard degenerate pieces. Preserve segment IDs on every tile fragment.

Factor reusable policy, candidate-query, and interval-state operations out of the viewport API. The tile pipeline queries its buffered bounds directly; it must not call the old viewport handler with its viewport-area and feature-limit restrictions. Reuse a spatial index, and precompute immutable per-zoom display runs where worthwhile. Do not scan the entire dataset for each tile.

Initial LOD settings are benchmark hypotheses:

| Zoom | Geometry and color detail |
|---|---|
| Below 10 | Basemap context only initially |
| 10–12 | Simplify to approximately 1 screen pixel; merge compatible intervals within a segment into longer display runs using coarse grade/distance bins |
| 13–14 | Approximately 0.5 pixel simplification and finer bins |
| 15–16 | Detailed interval colors with existing equal-value coalescing and minimal simplification |
| Above 16 | Overzoom zoom-16 tiles initially; raise maximum if geometry/selection checks require it |

Do not drop entire short paths merely because they are small. Do not merge across graph segment boundaries, eligibility, stair status, quality class, inclusion state, coverage-warning state, or building-access visibility. In particular, preserve excluded, disconnected, and beyond-budget transitions. The existing default building-access filter still removes hidden geometry before encoding.

At coarse zoom, grade color summarizes a run using its maximum absolute grade, quantized into a documented bin; this avoids averaging away short steep portions. Distance color uses a documented bin of representative midpoint distances and is approximate at those zooms. Start with 2-percentage-point grade bins and 250 m distance bins at zooms 10–12, halving these at zooms 13–14. Tune against feature counts and visual fidelity, version every change, and keep conservative interval inclusion exact even when distance color is summarized. Unknown distance stays unknown. Missing/uncertain terrain never acquires a fabricated grade. Detailed inspection always shows original samples. Add a short low-zoom legend note explaining summarized colors.

An oversized tile must not silently omit paths or truncate features. Measure worst-case tiles; if budgets fail, revise LOD or increase minimum display zoom explicitly. Keep zoom-11 support as a benchmark requirement before expanding to zoom 10.

## Frontend behavior

Replace the shared `path-intervals` GeoJSON source with a vector source and a named `paths` source layer. All grade, distance, quality, and stair styles consume that source. Selection, direction arrows, and start markers remain small independent GeoJSON sources.

When a start or routing option changes:

1. Cancel the pending snapshot request and advance the request generation.
2. Remove stale exploration colors and show “Calculating paths from this start…”; keep the basemap, selection, and requested marker available.
3. Request a snapshot. Ignore responses from earlier generations even if server work completed after cancellation.
4. Install the returned vector tile URL, update the snapped marker, and show tile-loading state until visible tiles finish. Panning while preparation runs uses the latest camera when tiles become available.

Allow panning and inspection while loading. Never display old-start colors as current results. Switching metric or visibility changes tile URL variants without snapshot preparation. Clearing the start immediately restores static gradient tiles; keep the existing behavior for distance mode without an active start. Palette-only changes stay client-side.

Use MapLibre source URL updates or source replacement with explicit source/layer rebinding. Verify old tile responses cannot populate a new context. Treat tile errors separately from empty/outside-coverage tiles. Coverage messaging comes from config and snapshot metadata rather than a GeoJSON collection status. Deduplicate click hits by segment ID because buffered fragments may appear in adjacent tiles. Inspection and snapping continue through full-resolution APIs, including paths hidden from tiles.

MapLibre supports vector-source bounds and maximum zoom with overzooming; configure these from dataset coverage. References: [source specification](https://maplibre.org/maplibre-style-spec/sources/) and [VectorTileSource API](https://maplibre.org/maplibre-gl-js/docs/API/classes/VectorTileSource/).

## Implementation sequence

1. **Rendering model and encoder:** extract typed display-run data and transformations; choose a maintained MVT encoder after checking compatibility and license. Evaluate reuse of the current contour MVT implementation, but avoid expanding a specialized contour encoder into a general geometry engine without justification. Add tile coordinates, clipping, quantization, and decode-based tests.
2. **Static tiles:** implement spatial queries, LOD, cache bounds, HTTP metadata, and static source integration. Benchmark before adding exploration. Verify building-access toggles and all styling layers.
3. **Exploration snapshots:** implement canonical keys, preparation, shared interval state, expiry, eviction, and request coalescing. Add dynamic tile variants while preserving exact inclusion and inspection semantics.
4. **Frontend lifecycle:** implement snapshot loading, cancellation, expiry recovery, marker updates, tile errors, and selection across tile boundaries. Remove viewport fetching from normal pan/zoom events.
5. **Validate and tune:** compare matched screenshots and timings, tune LOD, and document cache/memory costs. Keep legacy GeoJSON endpoints for diagnostics and exact comparisons until parity is proven; remove their frontend use when the tiled path passes acceptance.

## Verification and acceptance

Correctness tests cover XYZ bounds; tile orientation; buffered seams; clipping/quantization; stable segment identity; empty coverage; policy and classifier variants; unchanged inspection/snapping; no merging across semantic boundaries; unknown values; conservative inclusion; snapshot identity; expiration; memory eviction; concurrent misses; stale frontend responses; and dataset replacement. Decode MVT results and compare properties and geometry to known fixtures rather than just checking byte output.

Repeat urban/forest views at zooms 11, 13, and 15 in static gradient, explored gradient, and distance modes with identical start/budget to the prior benchmark. Record total visible tile bytes, feature/vertex counts, cold/warm tile generation, routing and snapshot preparation separately, browser parse/source load and request-to-frame time, pan reuse, concurrency, and retained cache memory. Include zoom 10 and worst-case dense tiles as additional cases. Use at least ten warm repetitions, alternate off/on comparison ordering, and isolate browser/server runs from unrelated experiments.

Provisional targets on the same machine and dataset:

- At least 80% less transferred payload for the urban zoom-11 view than the current uncompressed GeoJSON baseline; report both uncompressed and wire sizes fairly.
- At least 50% less browser source-processing time at urban zoom 11, with no material zoom-15 regression.
- Warm adjacent panning makes no routing searches and requests only newly needed tiles; a repeated view reuses cached responses.
- Record and expose start-change preparation latency; several seconds is acceptable, with a 10 s initial target and a clear loading/error state. Do not hold pan requests behind a global preparation lock.
- No loss of exact routing, inspection, hidden-path snapping, coverage warnings, or inclusion-state transitions. Coarse color summaries are explicit and visually checked.

These are acceptance targets, not predictions. If generalized tiles still carry too many semantic fragments, report that evidence and revisit aggregation before adding infrastructure.

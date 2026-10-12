# Tiled path rendering: implementation and measurements

Implemented on 2026-10-12. Path styling now uses a MapLibre vector source with a `paths` layer. Selection, inspection profiles, direction arrows, and start markers retain original geometry. Legacy GeoJSON endpoints remain available for diagnostics.

## Rendering and lifecycle

Tiles use extent 4096, an 8 CSS-pixel buffer at MapLibre's 512-pixel tile size, Mercator projection, clipping, integer quantization, and duplicate/degenerate removal by the encoder. Geometry simplification is 1 pixel at zooms 10–12, 0.5 at 13–14, and 0.125 at 15–16. Above 16, MapLibre overzooms. Below 10, only the basemap is shown.

Contiguous intervals merge only within a segment, equal semantic state, and equal color bins. Grade rounds magnitude upward to multiples of 2 percentage points at 10–12 and 1 at 13–14; the maximum magnitude in the merged run therefore determines its bin. Distance rounds original interval midpoint distances to the nearest 250/125 m. Missing and uncertain terrain receives no synthesized grade. Detailed colors retain original values. Inclusion, exclusion, disconnectedness, and coverage-warning transitions remain exact.

The encoder is [tilezen/mapbox-vector-tile](https://github.com/tilezen/mapbox-vector-tile), version 2.2, MIT-licensed and compatible with Python 3.11. Shapely handles general geometry operations. The specialized contour encoder remains independent.

Start and routing-policy changes cancel client requests, discard old sources, and show “Calculating paths from this start…”. Generation guards ignore stale responses. Every source replacement rebinds styling layers and removes its old source. Panning uses MapLibre's tile cache without application viewport requests. Metric and building-access visibility select variants of the same snapshot. Palette/emphasis changes remain client-side. A 410 triggers one snapshot recreation; repeated failures show a tile error.

## Cache and operational settings

Use **one API worker**. Snapshots and cache IDs are process-local; multiple workers require a shared store or deterministic reconstruction.

| Environment variable | Default |
|---|---:|
| `RUNMAP_PATH_TILE_CACHE_BYTES` | 67,108,864 |
| `RUNMAP_PATH_SNAPSHOT_CACHE_BYTES` | 134,217,728 |
| `RUNMAP_PATH_TILE_TTL` | 900 seconds |
| `RUNMAP_PATH_SNAPSHOT_TTL` | 900 seconds |
| `RUNMAP_PATH_PREPARATION_DEADLINE` | 10 seconds |

Encoded tiles and snapshots use byte-bounded LRU caches with TTLs. Concurrent identical misses share futures. Search computation releases the distance-cache lock, so a new start does not lock warm tile requests behind routing. Snapshots retain only read-only uint8 inclusion, float64 midpoint distance, and bool coverage arrays; original geometry/grades remain shared. Existing bounded distance caches and shared interval-boundary caches belong to the dataset separately.

Snapshot preparation checks its deadline during routing, connectivity, and interval materialization and returns 504 on timeout. The initial policy setup and snap operation precede those cooperative checks. Cache eviction drops snapshot array references. Expired/evicted snapshot requests return 410 even when encoded tile bytes remain cached. Valid empty tiles return 200.

Rendering URL versions include the measurement artifact checksum, classifier access policy, classifier version, and entrance limit, in addition to the rendering algorithm version. Replacing measurements while retaining the same graph changes the immutable URLs. Static responses use public immutable caching; exploration responses use private max-age capped by remaining snapshot lifetime. Gzip middleware uses compression level 5. Responses include `Vary: Accept-Encoding`, a weak content ETag valid across compression variants, compute time, cache-hit status, and retained cache byte counts. POST responses use `no-store`.

## Server measurements

[Raw measurements](25-tiled-path-benchmark.json) cover urban and forest views at 10, 11, 13, and 15, all three display modes, ten warm repetitions, and alternating building-access visibility order. Bounds/start/budget match the prior benchmark where applicable. Complete intersecting tiles are counted, including buffered fragments outside the viewport; the legacy baseline counted viewport-intersecting features. Features duplicated across tile boundaries are included in counts.

Default-filter urban results, decimal MB:

| Zoom | Mode | Tile MB | Gzip MB | Features | First view ms |
|---|---|---:|---:|---:|---:|
| 10 | static grade | 4.86 | 1.22 | 138,772 | 2,314 |
| 11 | static grade | 5.03 | 1.37 | 144,284 | 3,070 |
| 11 | explored grade | 5.62 | 1.41 | 144,704 | 3,053 |
| 11 | distance | 3.39 | 0.87 | 73,687 | 2,111 |
| 13 | static grade | 2.65 | 0.83 | 80,239 | 1,330 |
| 15 | static grade | 0.43 | 0.19 | 9,644 | 186 |

Urban zoom-11 static tiles are **90.5% smaller uncompressed** than the previous filtered 52.94 MB GeoJSON result (91.3% smaller than unfiltered 57.63 MB). Gzip tile sizes are measured separately; no comparison against uncompressed JSON is presented as a wire-compression gain. Worst individual tile sizes, vertices, all modes, and raw warm timings are in the JSON. First views can reuse tiles from earlier overlapping views; this is not a claim that every recorded first view is cold.

Warm cached tile-set lookups are around 0.01–0.03 ms in process for the urban 11–15 views. These exclude HTTP, decompression, parsing, and browser rendering. Cold encoding still takes seconds at broad zooms; this implementation primarily improves payload/source cost and reuse, rather than guaranteeing faster cold generation. The encoded cache retained 66,875,890 bytes at the end of the run.

Snapshot preparation took 2.436 seconds at the fixed start and 6 km budget, retaining 2,788,902 bytes including a conservative fixed metadata allowance. This does not include shared dataset allocations, Python allocator overhead, or the separate distance cache. The response now additionally separates routing and interval preparation timings.

Reproduce with:

```sh
uv run --extra web python -m runmap.experiments.benchmark_path_tiles \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  docs/25-tiled-path-benchmark.json --repeats 10
```

## Browser verification

Headless Chrome 154 smoke checks verified requested/snapped markers on a hidden driveway, calculating state, metric and visibility changes without snapshot preparation, panning without routing requests, rapid consecutive starts resolving to the latest start, and clearing to static tiles. No legacy viewport/explore requests or browser errors occurred in the final smoke run. Simulated 410 errors verified exactly one recreation and retry protection; [raw smoke results](25-tiled-path-browser-smoke.json) retain these assertions.

The paired browser benchmark uses matched cameras, a blank background with one styled line layer, preloaded GeoJSON and vector bytes, ten warm repetitions, and alternating source ordering. In-memory tile delivery isolates source processing. A separate sequence measures HTTP-backed vector source installation to two animation frames; it includes browser caching and source processing. Serial tile prefetch timing is recorded separately and is not request-to-frame latency. Headless software rendering and the simplified common style limit conclusions about full application GPU presentation.

Reproduce with the API on port 8000, Vite on 5173, and headless Chrome remote debugging on 9225:

```sh
pnpm --dir web dev --host 127.0.0.1
node web/scripts/benchmark-path-tiles.mjs docs/25-tiled-path-browser-benchmark.json
```

The companion [browser smoke script](../web/scripts/check-path-tiles.js) can be evaluated through Chrome DevTools with the test driver's exposed `window.__map`.

Paired warm source medians in milliseconds, GeoJSON → vector:

| View | Zoom | Static grade | Explored grade | Distance |
|---|---:|---:|---:|---:|
| urban | 11 | 1422.8 → 144.1 | 1478.2 → 158.3 | 1304.7 → 98.6 |
| urban | 13 | 619.6 → 94.1 | 644.9 → 99.1 | 613.8 → 64.3 |
| urban | 15 | 84.9 → 52.9 | 86.2 → 53.0 | 84.2 → 52.6 |
| forest | 11 | 1488.1 → 161.4 | 1546.9 → 177.9 | 1359.2 → 105.6 |
| forest | 13 | 576.5 → 94.6 | 604.0 → 98.3 | 536.8 → 62.9 |
| forest | 15 | 54.0 → 41.7 | 53.6 → 42.2 | 53.1 → 41.9 |

These matched-camera measurements meet the provisional 50% urban zoom-11 source-processing improvement, with no zoom-15 source regression. This run used a **1280 × 900** map canvas, larger than the prior 756 × 469 experiment, so compare the paired measurements within this run rather than their absolute times against the earlier browser table. The blank background and common line style isolate processing rather than reproducing the complete application style. Median warm HTTP-backed source-to-frame times were about 133–183 ms at urban zoom 11 and 83 ms at urban zoom 15. They do not include a new routing snapshot or a cold server.

[Raw browser measurements](25-tiled-path-browser-benchmark.json) retain every source/frame sample, HTTP sample, bounds, tile payload count, and browser identity. Measurements were isolated from the server benchmark. The artifacts record the rendering URL format before its final checksum/access-policy identity hardening; that change does not alter geometry, encoding, LOD, or measured source work.

## Correctness checks and limits

Decode-based tests cover XYZ validation/orientation, original segment IDs, missing values, buffered clipping, conservative coarse bins, inclusion transitions, exact snapshot identities, immutable arrays, deadlines, byte eviction, TTL expiration, cache coalescing, independent-start concurrency, ETags, cache policy, HTTP errors, and dataset object replacement. All 114 Python tests pass, including the existing routing, snapping, inspection, and building-access checks. The frontend production build passes with its existing bundle-size warning.

The low-zoom legend explains summarized colors. No feature-count cap or payload truncation is applied. Broad grade tiles still contain many semantic fragments; future optimization should preserve these boundaries. The payload and isolated source-processing targets are met. Full-application GPU frame timing, repeated cold HTTP timings, and systematic screenshot comparisons across every policy remain broader validation work; these measurements do not establish those claims.

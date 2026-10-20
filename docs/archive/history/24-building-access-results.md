# Building-access filter: implementation and measurements

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

Measured on 2026-10-12 using the prepared Leppävaara dataset. The filter is default-on for all prepared path layers, with independent full-graph snapping and routing.

## Classification

- 6,097 / 69,707 segments hidden: 4,930 driveway segments and 1,167 entrance segments.
- 20,410 / 278,839 measurement intervals hidden (7.3% dataset-wide).
- Classification and display-index construction: 0.425 s; baseline load: 4.354 s.
- Additional retained Python allocations: 3,801,109 bytes (3.63 MiB); peak additional allocations during construction: 22,258,871 bytes (21.23 MiB), measured separately with tracemalloc.
- Identity includes the graph digest, public pedestrian access policy, classifier version, and entrance-chain limit. Classification runs once during loading, with no viewport-dependent topology work.
- The 150 m limit applies to the whole compatible terminal entrance chain. Branched entrance trees use a conservative total-subtree limit. A boundary touch protects the whole dangling tree. Geometry-envelope coverage, fully peeled components, turning loops, and ambiguous branches remain visible.

## Server comparison

Unfiltered → filtered, gradient mode without a start. Bytes are uncompressed JSONResponse payload sizes. Times cover the in-process API query, interval coalescing, and serialization. Cold means cleared profile, distance, and interval-boundary caches after dataset load; ten warm repetitions follow. Full cold, median, p95, candidate, interval, feature, and vertex results for all three modes are in [the raw server measurements](24-building-access-benchmark.json).

| View | Zoom | Intervals | Payload MB | Warm median ms | Warm p95 ms |
|---|---:|---:|---:|---:|---:|
| urban | 11 | 195,069 → 179,259 | 57.63 → 52.94 | 1712.6 → 1643.8 | 2358.0 → 2264.2 |
| urban | 13 | 42,862 → 40,402 | 12.65 → 11.92 | 230.3 → 241.6 | 868.0 → 899.6 |
| urban | 15 | 6,189 → 5,944 | 1.82 → 1.75 | 28.0 → 27.2 | 28.6 → 28.6 |
| forest | 11 | 176,264 → 161,935 | 52.06 → 47.82 | 1376.1 → 1319.9 | 1964.2 → 1344.8 |
| forest | 13 | 30,234 → 26,715 | 8.94 → 7.91 | 143.9 → 128.7 | 667.0 → 647.0 |
| forest | 15 | 3,353 → 2,888 | 0.99 → 0.85 | 22.5 → 21.5 | 627.8 → 30.1 |

Urban payload reduction is about 4–8% across zooms and modes. At urban zoom 11, exploration median time falls from 3,095 to 2,835 ms for gradients and from 3,032 to 2,895 ms for distance. Urban zoom-13 gradient medians regress in this run; timings are noisy and no universal latency improvement is claimed. The broad zoom-11 responses still contain tens of megabytes, so this filter alone does not solve low-zoom rendering cost.

Fixed viewport bounds, start (24.80259, 60.21682), 6 km budget, dataset digest, machine, Python version, and policy are recorded in the JSON. Urban and forest labels describe viewport centers; broad viewports overlap and include mixed land use. Browser interaction smoke checks overlapped part of the server run, so these local timings are diagnostic rather than an isolated performance guarantee.

Reproduce the server measurements:

```sh
uv run python -m runmap.experiments.benchmark_building_access \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  docs/24-building-access-benchmark.json --repeats 10
```

## Browser comparison

Completed all 18 viewport/zoom/mode comparisons in Headless Chrome 154 on the same local API and dataset. Each filter setting has one initial sample followed by three warm samples. The map canvas was 756 × 469 CSS pixels. Automatic viewport requests were disabled to isolate the measured source updates. The source retained gradient styling for all payload modes; these measurements compare shared GeoJSON processing, rather than every mode’s final styling.

Source update measures `setData` through `sourcedata.isSourceLoaded`. Total measures HTTP request, JSON parsing, source update, and two animation frames; it is a rendering proxy, not proof of completed GPU presentation. Initial samples are not cold-cache measurements: server caches may already be warm. All individual samples, bounds, browser identity, and dataset digest are in [the raw browser measurements](24-building-access-browser-benchmark.json).

Warm medians in milliseconds, unfiltered → filtered:

| View | Zoom | Payload mode | Source update ms | Request to frame ms |
|---|---:|---|---:|---:|
| urban | 11 | gradient | 750.7 → 675.1 | 3116.4 → 2633.6 |
| urban | 11 | gradient explore | 769.3 → 706.2 | 3766.2 → 4149.7 |
| urban | 11 | distance explore | 756.1 → 676.6 | 4466.5 → 3817.2 |
| urban | 13 | gradient | 153.0 → 148.0 | 533.6 → 516.9 |
| urban | 13 | gradient explore | 156.8 → 152.0 | 698.9 → 1133.1 |
| urban | 13 | distance explore | 156.5 → 145.3 | 700.1 → 632.7 |
| urban | 15 | gradient | 19.9 → 19.0 | 83.4 → 83.5 |
| urban | 15 | gradient explore | 21.5 → 21.3 | 116.7 → 99.8 |
| urban | 15 | distance explore | 20.9 → 19.2 | 100.1 → 99.6 |
| forest | 11 | gradient | 669.8 → 634.1 | 2616.0 → 2616.6 |
| forest | 11 | gradient explore | 704.6 → 691.4 | 3967.3 → 3866.5 |
| forest | 11 | distance explore | 711.6 → 648.2 | 4480.7 → 4216.1 |
| forest | 13 | gradient | 110.3 → 92.8 | 400.3 → 333.6 |
| forest | 13 | gradient explore | 119.6 → 94.5 | 966.4 → 466.6 |
| forest | 13 | distance explore | 102.1 → 93.3 | 500.5 → 450.5 |
| forest | 15 | gradient | 10.6 → 9.4 | 66.6 → 66.7 |
| forest | 15 | gradient explore | 11.7 → 9.5 | 83.3 → 66.4 |
| forest | 15 | distance explore | 12.2 → 10.0 | 83.3 → 83.4 |

Median source-update time decreases in all 18 comparisons. Urban zoom-11 source updates improve by approximately 8–11%; urban zoom-15 savings are only 0.2–1.7 ms. Forest source-update savings range from roughly 2–21%. Total latency is mixed: urban gradient exploration regresses at zooms 11 and 13, and several detailed views are effectively unchanged. This supports reduced source-processing work, but not a universal end-to-end speedup.

Only three warm browser samples were collected per setting, always with filtering off before on. These diagnostic measurements are sensitive to cache state, garbage collection, server variability, and ordering; they do not establish statistical significance or representative p95 latency. Broad views still take seconds to fetch and render. The server benchmark above provides ten-sample warm timing distributions.

## Validation

- Python suite and focused additions: 102 tests passing across topology, paths, routing, exploration, API, and web tests.
- `pnpm --dir web build` passes; Vite retains its bundle-size warning.
- Headless Chrome 154: default checkbox, restoring hidden geometry, gradient/distance switching, panning, selection retained across toggles, and hidden-branch start snapping all passed.
- Inspected urban driveway `w8653272p3`: 34 m service driveway with gravel surface. Its marker snapped at 60.2147194, 24.8034416 with 0 m offset while its colored geometry stayed hidden; inspection reported a separate visibility reason.
- Inspected entrance footway `w1001726096p1` is hidden (7.15 m, fine gravel). Forest mud path `w172197762p0` and residential Timpurinkuja segment `w4235780p0` remain visible and legally eligible.
- Synthetic coverage includes entrance footways/stairs, branched entrances, mixed forest/residential siblings, through footways, scenic dead ends, long entrance chains, tree-only components, coverage uncertainty, parallel segments, self-loops, loop connectors, pedestrian restrictions, prepared boundaries, and surface/stair invariance.
- API regression checks prove hidden intervals never reach interval-state evaluation, all-hidden queries are empty ready responses, limits apply after filtering, toggling shares routing caches, and inspection and snapping still work.

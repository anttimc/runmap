# Roadmap

Outstanding work, reviewed 2026-11-05. See the [guide](guide.md) for delivered
features and [architecture](architecture.md) for implementation details.
Implementation, automated checks, and local runner acceptance are separate.
Record reproducible inputs, dataset/model/settings identities, and machine/toolchain
details for reviews and benchmarks. Preserve dated reports as evidence.

Environment exploration and costs are next. Validation of existing features is
deferred to the end of this backlog; it does not block environment work.

<a id="planned-environment-preferences-experiment"></a>

## Next: environment exploration and costs

Priority: explore the environment fields in the main map, then integrate segment
features, routing costs, and weight controls. Noise learning is a separate later
track and does not block this work. Checks for new environment behavior stay with
the implementation steps below.

The [four-field raster baseline](guide.md#experimental-environment-rasters) exists.
Two independent tracks follow the [environment design](environment-preferences.md#two-development-directions).
Manual OSM audits, hand-labeled locations, and learning all four fields are not
prerequisites. Preserve known source/support limitations.

### Track 1: routing application integration

1. **Map-layer acceptance:** the capital-area source and regional raster are
   prepared from one [dated Finland OSM snapshot](archive/history/2026-11-05-shared-osm-snapshot.md).
   Bounded tiles, toggles, opacity, legends, and coverage indication are implemented;
   automated block-seam and tile checks pass. Verify real-data seams, overlap,
   accessibility, desktop/mobile behavior, and cold/warm interaction latency.
   [Pilot smoke checks and API observations](archive/history/2026-11-05-environment-tiles.md)
   do not establish full-area acceptance.
2. **Segment features:** project fields onto route segments with chainage,
   support, and artifact identities. Verify direct-grid agreement, partial and
   repeated traversals, bounded loading, and native/reference equivalence.
3. **Costs and controls:** define weights, defaults, unknown handling, Off behavior,
   and summaries alongside distance/hill/shape costs. All weights Off must
   reproduce existing routes; saved routes must show understandable individual
   and combined tradeoffs within the routing latency budget.

See [delivery, projection, and UI design](environment-preferences.md#track-1-routing-application-layers-and-segment-projection).
This track proceeds independently of noise learning.

## Later: OSM-only road-noise learning (Track 2)

1. **Reference targets:** acquire compatible municipal road-noise data for Espoo,
   Vantaa, and Helsinki, starting with the [Espoo map service](https://kartat.espoo.fi/ims).
   Confirm licensing, years, metrics/heights/methods, coverage, and banded/continuous
   values. Freeze aligned targets, OSM dates, spatial splits, and below-threshold/
   no-data handling; hold out a city where compatible data permit.
2. **Calibrated baseline:** fit current road-class intensity factors first,
   retaining attenuation/aggregation and the unchanged proxy as control. Report
   acoustic-unit alignment and held-out errors before the discomfort transform.
3. **Learned alternatives:** compare gradient boosting and an MLP, optionally a
   spatial CNN, using identical targets/splits and OSM-only inputs. Report
   city/class/noise-range errors, leakage controls, seeds/resources, and a model
   promotion or baseline-retention decision.
4. **Optional upgrade:** version a justified improvement and regenerate tiles and
   segment features with consistent coverage and measured preparation/serving costs.

See the [experiment protocol](environment-preferences.md#track-2-osm-only-road-noise-prediction).
Municipal maps are reference model outputs, not measured noise or comfort labels.

## Planned: routing improvements

- **Traffic-light avoidance:** retain crossing/signal/island/railway evidence;
  model directed passage and endpoint/repeated events, using search state where
  arrival/exit matters. Validate branching, multi-node, avoidable, and unavoidable
  crossings before exposing a control.
- **Guided assembly:** add waypoints, retained sections, and avoidance guidance.
  Validate directed walks, ordered constraints, repetition, and infeasible inputs.
- **Automatic suggestions:** offer bounded, geographically different complete runs
  with explicit constraints and local runner acceptance. The CLI anchor baseline
  remains experimental.
- **Review-driven tools:** consider reproducible request copies, advanced shape
  controls, field/repetition overlays, and longer inspection stretches.
  Cross-junction flat/hill discovery needs an explicit scope first.

<a id="next-sqlite-storage-and-bounded-network-loading"></a>

## Later: storage and routing performance

Selective SQLite loading, packed data, and bounded native working sets are
implemented. See [prepared data](reference/prepared-data.md#prepared-network-sqlite-database)
for contracts. Remaining work:

- Tune window size, expansion rules, and deployment limits using the deferred
  performance checks. Expansion is heuristic and can miss a better route outside
  the loaded area; lower-bound-based expansion is a later option.
- Reduce cold zoomed-out tile preparation; evaluate compact display chunks using
  complete browser tile-set timings. Preserve inspection, coverage, policy flags,
  and distance boundaries; see [existing measurements](archive/history/52-distance-tiles.md).
- Consider streaming/direct offline preparation to replace whole-JSON conversion
  when larger datasets justify it.

### Supporting work: shared integer indexing and compact prepared data

- Evaluate sharing measurement coordinates with segment geometry without changing
  sampled positions or tile seams. Revisit filtered adjacency only if measured
  search gains justify its storage.
- Reduce temporary JSON/feature decoding in the reference loader if measured
  latency or peak memory justifies it. Preserve shared policy topology, artifact
  identities, and local indices; measure memory and bounded-search time.
- Add byte-bounded chunk reuse across different working bounds, with dataset
  generation, concurrency, and ownership guarantees. Benchmark overlapping
  windows and long routes. Further incremental/hierarchical search, spatial
  batching, and caching should follow profiles and preserve valid cost bounds.

## Planned: terrain and maintainability

- Add a masked smoother or validated fill policy before processing genuinely
  incomplete DCT windows.
- Prepare missing smoothing products in bounded background work or precompute
  useful presets. Then measure contour simplification, sampling density, and
  persistent/display-cache improvements, in that order.
- Fix exactly flat terrain: valid zero magnitude must produce zero path grade
  despite undefined bearing, while genuine missing data stays missing.
- Finish precise annotations and focused configuration validation; separate
  operations from data containers and raster processing from persistence where
  useful. Preserve serialized contracts and add static checking when types are
  coherent; follow [AGENTS.md](../AGENTS.md).
- Add preparation implementation identity to provenance. Measure before adding
  cross-request field caches; preserve inspectable exports.
- Extract feature map layers from frontend interaction controllers; replace
  state carried implicitly by DOM values/status text with explicit state.

## Loop recommendation experiments

- Reproduce and fix archived snapped-endpoint offset errors from the
  [grid experiment](development.md#loop-length-parameter-grid), separately from
  unreachable cases.
- Develop residual uncertainty and network/partition features for polynomial
  estimates; add coefficient/gate uncertainty to the
  [MAP mixture](reference/loop-length-mixture.md). Current plug-in mixture spread
  does not establish information gain.
- Compare coarse-to-fine and model-guided/adaptive sampling under equal routing
  and wall-clock budgets. Define direction, target tolerance, and route-quality
  criteria before adding automatic turnaround selection.

## Optional / later

- **Hosting:** choose basemap/attribution, repeatable builds, bounded storage and
  misses, and deployment cache/concurrency limits. Object storage, CDN adapters,
  multiple workers, administrator acquisition jobs, and public acquisition
  controls remain undelivered; snapshots are process-local.
- **Coverage:** explicitly select and prepare bounds for wider public coverage
  or a private Jämsä region.
- **Features:** running-time models, target duration, route import/export, and
  movable-centre/enclosure controls.
- **Experiments:** a bounded Odin or Go implementation if justified by profiling
  or a learning objective.

Cycle-first topology, face search, and cycle catalogues are superseded. Their
unfinished [archived tasks](archive/README.md) are not scheduled.

## Deferred: validation and acceptance

Revisit these checks after environment exploration and costs, or when a concrete
issue makes one relevant. Preserve the existing routing acceptance recorded below.

### Route modes and elevation

The user accepted current routing quality and normal desktop use on 2026-11-04.
Basic desktop/mobile browser checks passed; see the
[review record](archive/history/2026-11-04-route-mode-ui.md). Further UI issues
are deferred, rather than blocking this feature:

- Review the shared-start status wording: a turnaround-selection prompt can
  replace the warning that the displayed route uses previous settings.
- When needed, broaden checks to pointer/touch endpoint and circle dragging,
  full keyboard-only use, hill help, and long settings in short viewports.
  Native/reference equivalence and performance remain in their sections below.

#### Route elevation: local acceptance

- Review [profiles](guide.md#route-elevation) on flat, hilly, closed-loop, and
  out-and-back routes in both modes, including hills Off. Check absolute heights,
  ascent/descent, partial/repeated stretches, gaps, and structure uncertainty.
- Check chart/map exploration, keyboard/mobile use, close/reopen, stale responses,
  orientation changes, and endpoint selection. Measure cold/warm profile latency
  and height preparation time/size.

<a id="next-correctness-and-local-acceptance"></a>

### Map and terrain

- Review contour smoothing Off/Light/Strong in forward-distance and extra-travel
  views around shorelines, crossings, narrow corridors, and disconnected paths.
  Preserve small detours and masked holes; compare generation times.
- Review flat corridors, short/sustained hills, rolling paths, bridges,
  underpasses, stairs, restrictions, missing surfaces, and source edges. Reuse
  the [source-derived fixtures](archive/history/30-routing-preferences-audit.json)
  and record accepted/rejected examples. Connectivity, measurements, and
  uncertainty should be understandable to a local runner.
- Update `web/scripts/check-distance-shells.mjs` for current layer switches and
  combined display. Verify both shell methods, cancellation, and shared-start
  edits with planning enabled and disabled.
- Validate whole-domain seams at the longest 100 m background preset; existing
  halo validation covers the 40 m default. Check ringing, boundaries, nodata,
  and detail/noise tradeoffs.

### Storage equivalence

- Compare tiles, inspection, snapping, exploration, and routes against reference
  results. Cover detours, near/aligned endpoints, crossing segments, partial
  traversals, direction/access rules, dead ends, unloaded connections, source
  edges, and expansion/deadline limits.

### Native routing and performance

Native traversal, Rust phase/allocation profiling, and integration comparisons
are delivered. Production integration now uses a 10 m spacing cap and at least
two midpoint samples per geometry piece, matching the Python reference while
retaining field-scale limits. See [routing mathematics](reference/routing-mathematics.md),
the [native profile](archive/history/2026-11-02-native-routing-profile.md), and the
[dated integration comparison](archive/history/2026-11-02-integration-comparison.md).
These measurements do not complete route-level or interaction acceptance.

- Verify clean builds across supported Python versions/platforms and the minimum
  Rust version. Broaden native/reference comparisons to short/long routes, moved
  endpoints, width changes, shape disabled, and hills enabled. Investigate changed
  paths, including near-equal alternatives and the revised sampling.
- Measure preparation time/size, startup and peak/retained memory, cold/warm tiles
  and routes, and two concurrent planners. Grow total coverage while holding
  request area fixed to check that active memory stays bounded.
- Report kernel, language-boundary, planner, and full API timings separately,
  with p50/p95, sample counts, expanded states, and loaded bytes. Use fresh request
  costs, changed inputs, and documented cache states. Check interpreter contention;
  release the reference scalar adapter's lock only if measurements justify it.
  Refresh profiles where needed, retaining older evidence.
- Measure browser endpoint/width/start/policy/budget changes, including bursts,
  queueing, network/serialization, rendering, cancellation, and stale responses.
  Establish visible-interaction budgets alongside the
  [100 ms backend target and acceptance matrix](100ms-routing-architecture.md#latency-budget-and-acceptance).

### Loop recommendation acceptance

- Validate [turnaround shading](distance-shells.md#estimated-turnaround-region)
  across new starts, widths, distances, and policies: target-length success,
  independent uncertainty coverage, wider search windows, responsive layout,
  shell overlap, dragging/stale responses, and cold/warm latency. The
  [offline polynomial comparison](archive/history/60-extended-width-polynomials.md)
  is complete; browser and local runner acceptance remain open.
- Review [grid-sampled routes](development.md#loop-length-parameter-grid) at
  contrasting locations/preferences.
- Test polynomial transfer and residual/network features on held-out regions
  and width slices, including switches, crossings, repetition, and target success.
- Validate the MAP mixture across starts, priors, and linear/RBF gates with nested
  tuning; check component occupancy, convergence, and interval calibration.

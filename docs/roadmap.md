# Roadmap

Outstanding work. Product direction reviewed 2026-11-12; detailed backlog last
reviewed 2026-11-11. See the [guide](guide.md) for delivered
features and [architecture](architecture.md) for implementation details.
Implementation, automated checks, and local runner acceptance are separate.
Record reproducible inputs, dataset/model/settings identities, and machine/toolchain
details for reviews and benchmarks. Preserve dated reports as evidence.

Environment exploration, costs, routing integration, and preference controls are
implemented. The local runner accepted environment route planning and the UI on
2026-11-06. Main-map visuals and rendering were accepted on 2026-11-05.

## Product priorities

The core workflow is preference-based route generation followed by sports-watch
export. Prioritize application responsiveness, then easier route shaping;
automatic suggestions come later. Map exploration, including finding training
hills and rough route design without optimization, remains a secondary purpose.
See [goals](goals.md) for audience and scope.

Named browser-local preference presets and shared run settings are implemented.
Keyboard/touch review and local runner acceptance of the settings workflow remain
outstanding. Cross-device preference sync, cloud access for sharing with friends,
and broader Finnish coverage need scope decisions before being scheduled. Public
hosting is a longer-term ambition subject to operating resources.
The detailed backlog below still needs review against these priorities; section
order does not establish execution order.

<a id="planned-environment-preferences-experiment"></a>

## Environment follow-up validation

The [four-field raster baseline](guide.md#experimental-environment-rasters) and
[environment routing controls](environment-preferences.md) are delivered.
Remaining reproducible validation work:

- Record saved regional requests with dataset/model/settings identities and
  compare route tradeoffs, lengths, and missing context across preferences.
- Record reproducible comparisons for traffic High=4.0, including detours,
  loop shape, and penalties for missing context. The local runner reported
  improved routing on 2026-11-08; saved-route calibration remains outstanding.
- Measure bounded join loading, expanded states, and routing latency with
  environment preferences enabled. User acceptance does not replace a recorded
  performance benchmark.

Noise learning remains an independent later track.

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

- **Traffic-light avoidance validation:** [passage preparation and Python/Rust
  routing](traffic-light-avoidance.md) are implemented. Review mapped stages against
  actual local crossings, especially crossings split across ways, independent stages
  within one way, branching, directional signals, and tram/island uncertainty.
  Resolve the [reported Slöörinpolku tram-signal source gap](traffic-light-avoidance.md#reported-tram-signal-coverage-gap-2026-11-11)
  through corrected mapping or provenance-tracked local corrections; refreshing
  the current OSM source alone does not supply its missing signal tags.
  Calibrate provisional Low=60 m / High=180 m detour weights using saved routes;
  record regional cold/warm latency, state expansion, memory, and local runner acceptance.
- **Route reshaping:** drag a route to a new ordered pin and reroute through it.
  Decide how much of the old route may change: retained geometry, extra length,
  or preference-cost limits; define hard/advisory limits and infeasibility.
  Preserve repeated-traversal ordering, cross-leg costs, and keyboard/touch access.
  See [design questions](routing-scalability.md#dragging-a-route-to-a-pin).
- **Automatic suggestions:** offer bounded, geographically different complete runs
  with explicit constraints and local runner acceptance. The CLI anchor baseline
  remains experimental.
- **Review-driven tools:** consider reproducible request copies, advanced shape
  controls, field/repetition overlays, and longer inspection stretches.
  Cross-junction flat/hill discovery needs an explicit scope first.

<a id="next-sqlite-storage-and-bounded-network-loading"></a>

## Next: routing responsiveness and scaling

Selective SQLite loading, packed data, and bounded native working sets are
implemented. See [prepared data](reference/prepared-data.md#prepared-network-sqlite-database)
for contracts. Follow the [scalability proposal](routing-scalability.md):

- Extend the [November 12 backend edit measurements](archive/history/2026-11-12-shared-store-measurements.md)
  to more locations, larger prepared coverage at fixed route size, and visible
  browser latency. Target runs up to 40 km with
  a revised route visible within one second of a settings change or pin release.
  Fix benchmark hardware, concurrency, network conditions, and percentile; report
  warm edits, cold-area requests, and process-cold requests separately.
- Extend the [packed-input measurements](archive/history/2026-11-13-packed-routing-inputs.md)
  to larger coverage, additional starts, cold storage, and sustained concurrency.
  Validate the compact hill arrays, compressed input loading, and topology-only
  generation across those workloads before making packed inputs standard.
  Reduce cold hill-channel activation and startup identity-scanning latency;
  restored warm-edit performance does not establish first-activation acceptance.
  Extend
  shared chunk reuse to avoid area-wide index assembly and serialize only related
  loads. Bound in-flight memory.
  Add cooperative cancellation and bounded
  admission for superseded edits. See [proposed boundaries](routing-scalability.md#proposed-architectural-boundaries).
- Prototype directed landmark lower bounds on a relaxed distance graph; validate
  overlays, access restrictions, signal states, and dataset identities. Compare
  full latency and memory against the current geometric heuristic.
- Evaluate bound-driven expansion or incremental loading after measuring chunk
  reuse and search improvements. Keep current heuristic expansion limits explicit.

Other remaining work:

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
- Extend the recorded chunk-reuse measurements to additional starts, genuinely
  cold storage, sustained concurrency, and larger datasets at fixed route size.
  Further incremental/hierarchical search, spatial
  batching, and caching should follow profiles and preserve valid cost bounds.
  Address oversized graphs that bypass the cache and active-channel preparation;
  preserve selected-route diagnostics. See the
  [distance-only corridor profile](archive/history/2026-11-11-distance-search-profile.md).

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

- **Suunto follow-up acceptance:** the user confirmed the Runmap export workflow
  and successful watch sync on 2026-11-11. Validate elevation display and review
  keyboard/touch dialog behavior and lost-response/selection recovery with the
  installed importer.
  Broader watch support and authenticated per-user hosting remain later work.

- **Hosting:** choose basemap/attribution, repeatable builds, bounded storage and
  misses, and deployment cache/concurrency limits. Object storage, CDN adapters,
  multiple workers, administrator acquisition jobs, and public acquisition
  controls remain undelivered; snapshots are process-local.
- **Coverage:** explicitly select and prepare bounds for wider public coverage
  or a private Jämsä region.
- **Features:** running-time models, target duration, route import, and
  movable-centre/enclosure controls.
- **Experiments:** a bounded Odin or Go implementation if justified by profiling
  or a learning objective.

Cycle-first topology, face search, and cycle catalogues are superseded. Their
unfinished [archived tasks](archive/README.md) are not scheduled.

## Deferred: validation and acceptance

Review these checks against the product priorities above, bringing forward those
needed to assess responsiveness or a concrete issue. Environment exploration and
costs are already delivered. Preserve the existing routing acceptance recorded below.

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

- If needed, complete keyboard-only and screen-reader review of the Environment
  layer controls. The local runner accepted their visuals, ordinary UI use, and
  rendering latency on 2026-11-05; see the
  [dated record](archive/history/2026-11-05-shared-osm-snapshot.md).

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

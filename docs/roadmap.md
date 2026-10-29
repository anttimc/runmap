# Roadmap

This is the authoritative list of outstanding work. Existing behavior belongs
in the [guide](guide.md). Implementation and user acceptance are separate:
historical passing tests and smoke checks do not complete local runner review.
The priorities below consolidate the previous plans without marking their
remaining acceptance gates complete.

## Next: one-way and round-trip planning

Local runner acceptance for the [delivered route modes](guide.md#plan-run)
remains outstanding:

- Review familiar one-way and round-trip routes with hills Off/Low/High, including
  directed paths without a return, moved endpoints, and loop-width changes.
  Record dataset/settings identities and explain any reference/native differences.
- Check keyboard-only use, endpoint and circle dragging, Fit run, shared-start
  edits from distance controls, and all layer combinations on desktop and mobile.
  Automated browser checks use controlled route responses; real route quality and
  local runner acceptance remain separate.
- Measure representative one-way and round-trip backend and visible interaction
  latency, including cold/warm requests and concurrent planners. Fewer searches
  alone do not establish responsive interaction.

### Deferred: planned-route terrain inspection

Route elevation/gradient plotting is postponed. The intended follow-up replaces
segment gradient/profile inspection with inspection of the selected complete
planned route, in travel order, for either planning mode. Current segment
inspection remains available. Before that follow-up, resolve absolute elevation
versus integrated relative height change,
preparation/storage implications, and the remaining scope of path metadata and
distance inspection. Route inspection must handle partial/reversed/repeated
traversals, gaps and uncertainty, independently of whether hill avoidance is on.

## Next: correctness and local acceptance

- Review Off/Light/Strong distance contour smoothing in both distance modes around
  shorelines, railway crossings, narrow corridors, and disconnected paths. Check
  that small detours remain visible and masked holes survive; compare generation
  times on representative budgets. Light (25 m sigma) is the implemented default;
  visual acceptance and representative latency measurements remain outstanding.

- Replace the remaining “minimum outing” wording in interval inspection with
  forward-distance terminology. The Distance from start label already says return
  distance is excluded. Acceptance: labels, help, inspection, and budget behavior
  consistently describe forward distance and make no return guarantee.
- Review familiar flat corridors, short/sustained hills, rolling paths,
  bridges/underpasses, stairs, restrictions, missing surfaces, and extract edges.
  Reuse the [source-derived fixtures](archive/history/30-routing-preferences-audit.json),
  recording accepted/rejected examples with endpoints, expected observations,
  dataset/model identities, and settings. Separate data defects from UI or
  numerical defects. Acceptance: measurements, connectivity, and uncertainty are
  understandable to a local runner.
- Check the current hill slider/help and combined planner interactions in desktop
  and mobile browsers, including keyboard use, cancellation, stale results,
  failure states, mode restoration, inspection, and Fit run. Historical browser
  reports include superseded hill models. Acceptance: current controls behave
  consistently and undesirable runs can be reproduced.
- Update `web/scripts/check-distance-shells.mjs` for the current layer switches,
  persistent path hit testing, and combined layer display. Remove its obsolete
  zero-path-tile assertion and validate shared-start changes with planning enabled
  and disabled. Acceptance: the browser script runs against current markup and
  verifies both shell methods, cancellation, inspection, and layer combinations.
- Measure current preparation size/time, startup memory, cold/warm tile delivery,
  browser responsiveness, and start/policy/budget changes on a recorded machine.
  Existing tile benchmarks demonstrate specific payload/source-processing gains;
  full-application frame timing and broad policy/screenshot review remain open.
  Acceptance: measured interaction performance supports exploration and regeneration;
  adopt numerical targets from representative observations.

## Next: SQLite storage and bounded network loading

Replace whole-dataset API loading with selective reads from a prepared SQLite
database. Use SQLite R-tree envelope queries; geometry refinement, clipping,
snapping, cost evaluation, and graph search stay in the application. SpatiaLite
and a database server are not required. Memory should follow the requested area,
network density, concurrency, and bounded caches rather than total coverage.

The exporter, prepared-feature persistence, and local-network readers are implemented in
`paths/database.py`; see [prepared data](reference/prepared-data.md#prepared-network-sqlite-database)
and [export instructions](development.md#export-a-sqlite-network-dataset).
The API supports selective SQLite config, static tiles, inspection, and snapping.
Exploration and run planning use native traversal over bounded numeric working
sets when a matching artifact is present, with the local Python graph backend
retained for compatibility. Numeric exploration snapshots retain independent
sparse distances instead of graph owners. Run planning uses expanding windows
with persisted hill features. Chunk reuse between different bounds remains open. The
full-JSON compatibility bridge is removed; selected SQLite deployments require
no source graph or measurement JSON at runtime. Without a selected database,
the existing JSON loader remains available for import/debug/reference use.

Packed local graph loading is implemented in `paths/local_network.py`. It retains
complete selected segments, endpoint nodes, directed arcs, original chainages,
prepared policy/classification/hill features, and dataset-wide heuristic scale.
Stable IDs map to each window's dense indices. Unloaded connections and source
coverage bands are separate markers. See the [local-network probe](archive/history/46-sqlite-local-network.md).

Bounded exploration uses the complete snapped segment's envelope plus the forward
distance budget divided by the global endpoint-distance scale. Reached frontiers
trigger retries under area, segment-count, and deadline limits. Preparation uses
connection leases to bound concurrency. The reference backend skips terrain loading
and charges its local graph and distances to the snapshot cache; numeric snapshots
retain only sparse distances, with chunk ownership charged to the graph cache. See the
[exploration report](archive/history/47-sqlite-exploration.md).

Run planning starts with an EPSG:3067 square centred at the endpoints' midpoint,
enclosing their endpoint-diameter circle enlarged by 50%. Its side is 1.5 times
their straight-line separation, with a configurable 1,000 m minimum.
Search visits to unloaded frontier nodes, candidate geometry in the
boundary band, or incomplete assignments with unloaded connections trigger box
doubling. The proximity band is the greater of 100 m and 5% of the shorter box
dimension. Defaults allow five retries, 2,500 km² area, and 650,000 selected
segments, sharing one deadline across snapping/loading/all legs/retries.
Attempts and boundary/source involvement are reported; exhausted limits return
`area_limited`, distinct from unreachable endpoints and source coverage flags.
All candidate legs are rerun on the same expanded graph. See the
[planner report](archive/history/48-sqlite-runs.md) for saved-route equivalence
and the loading/retry tradeoff.

Remaining validation and improvements:

1. Benchmark representative capital-area short/long routes and concurrent
   requests, cold-cache behavior, and browser interaction. Tune minimum box size,
   retry growth/proximity bands, and deployment area/density limits from those
   observations. Boundary expansion remains heuristic: a better route outside
   the area can remain undetected. Lower-bound-based expansion is a later option.
2. Reduce temporary hill/feature JSON decoding if measured request RSS or latency
   justifies it; retain the existing schema/model identities and local dense
   index semantics. Local window caching requires byte/concurrency/generation
   bounds and evidence that repeated requests benefit.
3. Reduce first-load display preparation for zoomed-out tiles. Compact tile-input
   caching improves repeated gradient views, but cold SQL/JSON reads remain costly;
   see the [distance-tile measurements](archive/history/52-distance-tiles.md).
   Evaluate prepared compact display chunks and complete browser tile-set timing,
   preserving inspection, coverage, policy flags, and distance boundaries.

Acceptance: compare tiles, inspection, snapping, exploration, and planner results
against the current implementation on controlled fixtures and saved routes.
Include barriers requiring detours, near/aligned endpoints, crossing segments,
partial endpoint traversals, one-way/access rules, genuine dead ends, unloaded
connections, source edges, and expansion/deadline limits. Benchmark import time,
database size, startup time, peak/retained RAM, cold/warm p50/p95 interactions,
and two concurrent planners. Grow total coverage while holding request area fixed
to verify that startup and active memory no longer scale with the whole dataset.
The initial exporter still parses the existing JSON and packs measurements during
offline conversion; streaming/direct preparation is subsequent work.

### Supporting work: shared integer indexing and compact prepared data

Use the existing integer graph as the shared internal addressing system rather
than retaining another independent representation for every subsystem. Keep
three data containers: an immutable prepared network, a policy view containing
eligibility, and request-local overlays/search state/cost caches. Operations stay
in module-level functions. External string IDs remain available for API contracts,
provenance, inspection, and deterministic route ordering.

Node, physical-segment, directed-arc, and measurement-interval indices are distinct.
Their ordering is fixed for one dataset and shared across policies. Arcs reference
physical segments and direction; segment offsets identify ranges in packed
geometry/interval arrays. Endpoint overlays append temporary IDs and retain the
original segment index and chainage range. They never renumber the base dataset.

The shared indexing/storage contracts are described in
[prepared data](reference/prepared-data.md#selected-windows-and-identities). Measurement samples now also use packed arrays
in the shared segment order, and display references use integer IDs; the
[storage report](archive/history/38-packed-measurements.md) records equivalence
and the retained-memory change.

Graph WGS84/projected geometry is also packed in shared segment/node order, with
read-only segment views and sparse endpoint overlays; the
[geometry report](archive/history/39-packed-geometry.md) records the memory change.
Arcs and outgoing/incoming adjacency are now packed too, with lazy external views
and shared policy masks. The [arc report](archive/history/40-packed-arcs.md) records
the filtered-adjacency comparison and retained-memory/search-time tradeoff.
Continue incrementally, removing each replaced representation. Packing supports
the local working set; it is not a prerequisite for database-backed delivery:

Spatial cell lists now use immutable integer buffers, sharing unchanged rows
between the complete and building-access-filtered views. The
[spatial report](archive/history/41-packed-spatial.md) records memory savings,
candidate-query overhead, and output equivalence.

Display metadata now shares measurement offsets and packs thresholds; the
[display report](archive/history/42-packed-display.md) records its retained-memory
and tile-time tradeoff. Node/segment metadata now uses packed numeric columns,
pooled strings, and lazy public records sharing ID maps and geometry; the
[metadata report](archive/history/43-packed-metadata.md) records capital-area
memory savings and output/search comparisons.

1. Measurement interval coordinates are packed independently; evaluate sharing
   segment geometry without changing sampled positions or tile seams.
   Keep policy topology shared; revisit filtered adjacency if representative
   capital-area timings justify its additional storage. Acceptance: remove redundant data copies,
   preserve tile/inspection semantics, and measure memory plus bounded-search time.
2. Reduce temporary record decoding during SQLite local graph loading if
   representative latency/RSS measurements justify it. Versioned prepared
   features and packed local arrays are implemented; temporary JSON/feature
   records still contribute to peak request memory on the reference backend.
   The numeric artifact already provides a separate native search path; further
   storage changes require representative benchmark justification.

Keep the reference backend's shared-index work separate from the delivered native
numeric backend. Avoid a generic feature registry. Measure memory savings and
search time for each storage change; integer dictionary keys alone do not
establish a compact representation.

## Next: native routing validation and interactive latency

The product goal is responsive route updates when moving the start or turnaround
or changing the centre-cost width. The learning goal is to implement a small
low-level numerical algorithm in Rust. Scalar integration and native numeric
traversal with cost evaluation are delivered. The numeric backend retains bounded
immutable working sets and sparse exploration snapshots; the Python backend
remains a correctness reference. See [architecture](architecture.md),
[build commands](development.md#native-integration-kernel), and the
[100 ms target and recorded measurements](100ms-routing-architecture.md).
Cached local timings do not establish instant backend or browser response.

### Broaden native validation and profiling

- Verify clean builds and runtime behavior across supported Python versions and
  platforms, including the declared minimum Rust version. The first build was
  verified locally on macOS ARM64 with Python 3.14 and Rust 1.99.1.
- Extend route equivalence checks to shorter/longer routes, moved endpoints, and
  width changes. Investigate any path changes, including near-equal alternatives.
  Existing unit checks cover scalar-reference agreement, reversed/repeated points,
  narrow/wide fields, invalid inputs, and expired/mid-calculation deadlines.
- Benchmark release builds with a warmed process and fresh request feature
  caches. Report kernel-only time, Python-to-Rust call/conversion time, complete
  planner time, and full API response time separately. Cover the existing profile
  route plus shorter/longer routes, moved endpoints, width changes, shape disabled,
  and hills enabled. Record p50/p95 latency, sample counts, expanded states,
  machine/toolchain, and dataset/settings identities. Include two concurrent
  planners and bursts of changing inputs to expose queueing and stale work.
- Measure contention and throughput with two concurrent planners. Native graph
  construction and search release the interpreter lock; the scalar integration
  adapter used by the reference planner still holds it. Evaluate releasing it
  for scalar calls only if measured benefits justify the overhead.
- Save fresh flamegraphs and cProfile reports alongside previous local profiles,
  preserving old evidence. Python profiling sees the native call as one frame;
  use Rust/native profiling only if its internal cost needs investigation.
  Extend the tracked `benchmark_integration` entry point and retain dataset,
  machine, toolchain, and settings identities with new reports. Keep the Python
  reference available explicitly; missing native support must remain visible.

### Assess interaction latency and the next boundary

Measure rapid endpoint moves and centre-width changes through the browser,
including network, serialization, queueing, route rendering, cancellation, and
stale-response suppression. Establish an explicit backend and visible-interaction
latency budget on the target machine before claiming instant response. Avoid
using repeated identical-request cache hits as the acceptance workload.

Native traversal already evaluates costs without per-edge Python callbacks and
caches immutable working sets for exact bounds. Expansion still rereads chunks
and builds another native index. Implement byte-bounded chunk reuse across bounds
only with generation, concurrency, and ownership guarantees; benchmark changed
endpoints, overlapping windows, and long runs. Further work on incremental search,
spatial batching, or hierarchical acceleration should follow profiles and preserve
request-specific costs and valid lower bounds. The
[100 ms architecture](100ms-routing-architecture.md#latency-budget-and-acceptance)
defines the backend target and outstanding acceptance matrix.

## Planned: routing improvements

Deliver these independently after their data and validation gates pass:

| Increment | Required work and acceptance |
| --- | --- |
| Traffic-light avoidance | Preserve crossing-way, modern signal, island, and railway evidence; define directed passage events and endpoint/repeated-passage semantics. Use search state when charging depends on arrival/exit. Validate multi-node, branching, avoidable, and unavoidable crossings before exposing a control. |
| Busy-road preference | Acquire complete buffered road context independently of runnable eligibility. Define a bounded distance-decaying exposure proxy and coverage mask; review parallel paths, crossings, and grade separation. |
| Green-surroundings preference | Acquire complete candidate ways and multipolygon geometry with coverage. Select documented inclusion rules and review parks, woods, edges, private land, and green paths beside busy roads. Missing context must stay unknown. |
| Combined calibration | Compare each supported preference and combinations against Off; record distance, observations, uncertainty, expanded states, and latency. Expose only supported controls and require understandable local tradeoffs. |
| Guided assembly | Add waypoints, retained sections, and explicit avoidance guidance. Validate complete directed walks, ordered constraints, repetition, and infeasible requests against saved familiar outings. |
| Automatic suggestions | Build a bounded set of useful, geographically different complete runs with user guidance. The existing CLI anchor baseline is experimental evidence. Acceptance requires stated constraints, meaningful alternatives, and local runner review. |

Feedback tools such as copied reproducible request records, advanced shape
settings, field/repetition overlays, and longer inspection stretches remain
follow-ups driven by the local review. Cross-junction flat/hill discovery needs
explicit scope before implementation. Route terrain inspection is deferred as
described [above](#deferred-planned-route-terrain-inspection).

## Planned: terrain and maintainability

- Repeat the whole-domain seam comparison at the longest 100 m background
  preset; the recorded 512 m halo validation covers the 40 m default.
- Move missing smoothing products out of synchronous request handling into bounded
  background work, or precompute useful presets. Then measure contour simplification,
  sampling density, and persistent/display-cache improvements in that order.
- Continue representative terrain checks for ringing, boundary effects, nodata,
  and noise/detail tradeoffs. A masked smoother or validated fill policy is
  required before processing genuinely incomplete DCT windows.
- Resolve exactly flat terrain samples: derived zero magnitude has undefined
  bearing, and the path sampler currently rejects that nodata bearing. Acceptance:
  a valid flat cell yields zero path grade without inventing a terrain direction,
  while genuine missing data stays missing.
- Complete precise annotations and focused configuration validation; move
  operations out of data containers and separate raster processing/persistence
  where useful. Follow [AGENTS.md](../AGENTS.md), preserve serialized contracts,
  and add static checking when domain types are coherent.
- Add preparation implementation identity to provenance. Measure before adding
  cross-request field caching; preserve inspectable exports during SQLite migration.
- Continue frontend separation by extracting inspection/chart rendering and
  feature map layers from the interaction controllers. Make remaining feature
  state explicit where DOM values or status text currently carry it.

## Loop recommendation experiments

The [loop-length grid experiment](development.md#loop-length-parameter-grid)
samples turnaround position and width using the existing planner. Remaining work:

- Review sampled route quality with a local runner; repeat at contrasting
  locations and preference presets before drawing general conclusions. The
  [first grid report](archive/history/54-loop-length-grid.md) records the initial
  measurements and visual inspection.
- Resolve snapped-endpoint offset validation errors recorded by the grid, using
  the archived failing inputs separately from unreachable routes.
- Extend validation of the [initial polynomial baselines](archive/history/55-loop-length-baselines.md)
  beyond the [Pasila repetition](archive/history/56-pasila-loop-length.md) to
  additional starts and preference presets; test coefficient transfer between
  starts. Test a residual uncertainty model and
  network/partition features using held-out spatial regions and width slices;
  establish calibrated uncertainty near route switches and crossings. Check
  target-length success and route repetition.
- Compare coarse-to-fine sampling and model-guided search under equal routing-call
  and wall-clock budgets. Choose direction semantics, target tolerance, and route
  quality criteria before adding automatic turnaround selection to the app.

## Optional / later

Public hosting needs a suitable basemap arrangement and attribution, repeatable
data builds, bounded misses/storage, and deployment-specific cache/concurrency
decisions. Current exploration snapshots are process-local. Object storage,
CDN adapters, multiple API workers, administrator acquisition jobs, and public
acquisition controls are not delivered infrastructure.

Wider public coverage or a private Jämsä region needs explicitly selected bounds
and preparation. Running-time models, target-duration tuning,
route import/export, and movable-centre/enclosure controls
remain optional increments. A bounded Odin or Go experiment is optional when
profiling or a learning objective justifies it.

Cycle-first topology, face search, and cycle catalogues are superseded; their
unfinished tasks in the [archive](archive/README.md) are not scheduled.

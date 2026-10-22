# Roadmap

This is the authoritative list of outstanding work. Existing behavior belongs
in the [guide](guide.md). Implementation and user acceptance are separate:
historical passing tests and smoke checks do not complete local runner review.
The priorities below consolidate the previous plans without marking their
remaining acceptance gates complete.

## Next: correctness and local acceptance

- Correct the Explore status label that still describes a round-trip outing.
  Acceptance: labels, help, inspection, and budget behavior consistently describe
  forward distance and make no return guarantee.
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
`paths/database.py`; see [architecture](architecture.md#sqlite-storage-foundation)
and [export instructions](development.md#export-a-sqlite-network-dataset).
The API supports selective SQLite config, static tiles, inspection, and snapping.
Exploration, snapshots, and start-distance inspection use bounded local graphs.
Run planning uses expanding local graphs with persisted hill features. The
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
connection leases to bound concurrency, skips terrain loading, and charges both
the local graph and its distance buffers to the snapshot cache. See the
[exploration report](archive/history/47-sqlite-exploration.md).

Run planning starts with the endpoints' EPSG:3067 box enlarged by 50% in total
width and height (25% per side), with a configurable 1,000 m minimum in each
dimension. Search visits to unloaded frontier nodes, candidate geometry in the
boundary band, or incomplete assignments with unloaded connections trigger box
doubling. The proximity band is the greater of 100 m and 5% of the shorter box
dimension. Defaults allow five retries, 2,500 km² area, and 200,000 selected
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

The current integer search/cost pipeline and packed hill storage are described in
[architecture](architecture.md). Measurement samples now also use packed arrays
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
   records still contribute to peak request memory. Separate mapped numeric
   sidecars require benchmark justification.

Do not introduce a generic feature registry or move graph ownership/search into
Rust as part of this migration. Those are separate decisions supported by later
measurements. Numeric packing provides the expected memory saving; changing
dictionary keys to integers alone does not establish one.

## Next: Rust integration kernel and interactive routing

The product goal is responsive route updates when moving the start or turnaround
or changing the centre-cost width. The learning goal is to implement a small
low-level numerical algorithm in Rust. The first per-geometry kernel is delivered;
its boundary and behavior are in [architecture](architecture.md), build commands
in [development](development.md#native-integration-kernel), and initial evidence
in the [dated report](archive/history/33-rust-integration-kernel.md). Kernel
speedup alone does not establish instant backend or browser response.

Python A* now uses a prepared integer index with shared endpoint overlays. The
[integer-index report](archive/history/34-integer-search-index.md) records its
separate benchmark; this does not move search or per-edge costs into Rust.

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
- Measure whether releasing the interpreter lock benefits the existing two-planner
  concurrency case without adding excessive overhead to tiny calls. The current
  binding holds it; field results remain request-local.
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

If the remaining Python work misses that budget, profile again and consider
moving A* together with cost evaluation and its feature cache into Rust. Avoid a
native search that calls Python for every edge. Persistent native graph data,
incremental search, and spatial batching are later decisions supported by that
profile, rather than prerequisites for the first kernel. Use the existing dense
IDs as the starting point for a native graph boundary, preserving their lifetime
and overlay semantics instead of converting the full graph on each interaction.

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
follow-ups driven by the local review. Cross-junction flat/hill discovery and
absolute-elevation profiles need explicit scope before implementation.

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

## Optional / later

Public hosting needs a suitable basemap arrangement and attribution, repeatable
data builds, bounded misses/storage, and deployment-specific cache/concurrency
decisions. Current exploration snapshots are process-local. Object storage,
CDN adapters, multiple API workers, administrator acquisition jobs, and public
acquisition controls are not delivered infrastructure.

Wider public coverage or a private Jämsä region needs explicitly selected bounds
and preparation. Running-time models, target-duration/length tuning, automatic
turnaround selection, route import/export, and movable-centre/enclosure controls
remain optional increments. A bounded Odin or Go experiment is optional when
profiling or a learning objective justifies it.

Cycle-first topology, face search, and cycle catalogues are superseded; their
unfinished tasks in the [archive](archive/README.md) are not scheduled.

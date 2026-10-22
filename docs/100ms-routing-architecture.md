# Routing toward a 100 ms request budget

Status: run planning now uses native numeric traversal when a matching numeric
artifact is available. The API preserves snapping, costs, response fields,
request deadlines, and automatic window expansion. Immutable working sets have
a bounded LRU cache. Distance exploration integration and chunk reuse between
different working areas remain outstanding. See the
[numeric format and benchmark commands](numeric-routing-format.md).

The representative Leppävaara API request, including all three window attempts,
route rendering, and JSON serialization, measured 48 ms median with hills off
and 62 ms with hills high across ten repeated requests. Corresponding p95 values
were 50 ms and 64 ms; first requests were 505 ms and 464 ms. Startup was about
4 ms, accounted retained working sets about 103 MiB, and peak process RSS about
266 MiB. OS cache was uncontrolled. Samples recompute searches and responses;
there is no exact-response cache. HTTP transport/compression is excluded. These
observations establish a cached-case improvement, not a general 100 ms guarantee. A farther
turnaround at `(24.84, 60.24)` completed using 71,326 selected segments with the
raised 200,000-segment API cap (five retries). Three repeated requests measured
229 ms median and the first request 814 ms. The default 128 MiB working-set cache
cannot retain both expanded graphs in that case, illustrating why shared chunk
residency remains necessary for longer runs.

Reproduce with `benchmark_run_latency` and the numeric sibling artifact present:

```sh
uv run python -m runmap.experiments.benchmark_run_latency \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --hills high --repeats 10 \
  --report artifacts/pathfinding-profile/native-api-high.json
```

## Observed limits

The representative request uses home `(24.795, 60.216)` and turnaround
`(24.815, 60.225)` against the Helsinki capital-area SQLite dataset. It expands
through windows containing 4,313, 15,353, and 44,366 segments. After eliminating
unused feature loading, three-run median request times were approximately 2.0 s
with hills off and 4.9 s with hills high. Responses matched the earlier planner.
These are local observations, not latency guarantees.

Allocation measurements of an individual local window, with raw measurements
and coverage scans disabled, were:

| Segments | Peak traced allocations, hills off | Peak traced allocations, hills on |
| ---: | ---: | ---: |
| 4,313 | 17.6 MiB | 28.8 MiB |
| 15,353 | 64.0 MiB | 105.3 MiB |
| 44,366 | 189.1 MiB | 317.5 MiB |

For the largest window, retained traced allocations were approximately 47 MiB
and 75 MiB respectively. Tracemalloc includes tracked numeric buffers; these
figures are neither object counts nor total process RSS and exclude SQLite and
OS caches. Timing and traced allocation runs were separate.

The reference backend chunks store JSON topology. Each request decodes dictionaries, lists,
strings, coordinates, and scalars; collects all selected records; builds dense
indices; projects coordinates; and packs metadata into arrays. Hill data also
passes through interval and feature dataclasses before becoming arrays. Retries
repeat preparation for overlapping windows. A chunk size bounds a decode batch,
not total request allocations. Segment-count and time limits prevent unbounded
requests but do not make preparation fast.

## Required storage and runtime

Persist numeric search data offline in independently addressable chunks:

- Stable numeric node, segment, and directed arc identifiers.
- Chunk-local adjacency offsets and arc columns, with explicit cross-chunk
  endpoint references. A crossing connection must remain distinguishable from a
  physical dead end or a missing source connection.
- Projected node coordinates and geometry points with offset arrays.
- Eligibility masks for each supported profile, including node access and
  prepared boundary restrictions.
- Directional hill totals for complete segment traversals. Keep interval arrays
  available for endpoint partial spans; do not decode every interval into objects.
- Bounds and provenance identifying dataset, chunk format, and feature models.

Use a versioned binary representation with explicit dtypes, byte order, shapes,
length checks, and integrity validation. Uncompressed, aligned arrays can be
mapped read-only. Compressed chunks require a bounded decoded-buffer cache and
must include decompression time in the cold-request benchmark. Persist descriptive
metadata separately for selected-route rendering and inspection.

The search runtime must traverse these arrays directly. Rust is already used for
shape-field integration; extend that native boundary to graph traversal and cost
evaluation. Avoid a Python callback, named tuple, string lookup, or dictionary
allocation per visited arc. Keep the existing Python planner as a correctness
reference during migration.

A request owns endpoint overlays, request-specific shape integration, and search
state. Shared chunks remain immutable. Evaluate shape costs lazily for traversed
segments and reuse them between outbound/return searches. Whole-segment hill
costs use directional totals; endpoint overlays integrate the corresponding
partial interval ranges. Preserve one-way rules, policy exclusions, cost formulas,
frontier reporting, and deadline behavior.

Prefer chunk-local identifiers plus explicit references over rebuilding a global
dense graph for each window. A contiguous regional assembly is an acceptable
intermediate implementation if it copies numeric columns in bulk and benchmarks
within budget; it must not reconstruct per-edge Python records.

Window expansion retains already loaded chunks and search-independent buffers.
Maintain byte-bounded shared chunk residency with eviction; neither all-dataset
preloading nor an unbounded object cache is acceptable. Cache identity includes
dataset generation and feature versions. Request-specific overlays and costs must
never mutate shared buffers. Mapping lifetime must outlive every search or
snapshot retaining its data, including after cache eviction.

Distance exploration uses the same numeric chunks and native traversal. Its
reachable-node output and snapshot buffers have explicit byte limits; output
work necessarily increases with the amount of reachable data requested.

## Latency budget and acceptance

Treat approximately 100 ms as the target for complete server-side request
handling, measured from entry through response serialization, rather than only
an inner shortest-path call. A provisional allocation of that budget is:

| Stage | Budget |
| --- | ---: |
| Validation and snapping | 15 ms |
| Chunk lookup and buffer access | 20 ms |
| Endpoint overlays, costs, and searches | 50 ms |
| Selected-route metadata and serialization | 15 ms |

These are engineering budgets, not achieved measurements. Record stage timings
and end-to-end percentiles on specified hardware. Measure separately:

1. A fresh worker's first request, with startup reported independently.
2. Uncached application chunks with a warm OS file cache.
3. Cached chunks with changed endpoint locations and preferences.
4. A cold OS cache, under a documented procedure, where feasible.
5. Concurrent requests under the intended deployment worker count and memory cap.

Do not use exact-response cache hits to substantiate planning performance. Include
increasing home-to-turnaround separations around Leppävaara, both shape settings,
hills off/low/high, all supported surface/stair profiles, partial starts,
one-way paths, disconnected endpoints, and boundary expansion. Compare status,
snapped endpoints, legality, costs, observations, and candidate geometry against
the reference. Equal-cost tie handling must be deterministic; explain any
intentional route-selection changes.

Publish p50/p95/p99 latency, loaded chunks and bytes, visited nodes/arcs, retries,
peak RSS, and tracked allocations. Declare the supported spatial/distance and
response-size envelope. A strict 100 ms bound for arbitrary distances, physical
cold storage, or arbitrarily large exploration output cannot be inferred from
fast local queries. If native search itself exceeds budget, evaluate spatial or
hierarchical acceleration with valid lower bounds for request-specific shape and
reuse costs. Shortcuts based only on physical distance are not automatically
valid for the planner's changing costs.

## Implementation sequence

1. Add an end-to-end benchmark with explicit cold/warm modes and stage timings.
2. Export versioned numeric chunks, validate them offline, and measure direct
   buffer access independently of search.
3. Implement native traversal over chunks, including cross-chunk links and
   request endpoint overlays. Verify against the Python reference.
4. Integrate bounded chunk residency, incremental window expansion, and the
   shared distance-exploration path.
5. Run the acceptance matrix and report the supported 100 ms envelope before
   making that performance claim.

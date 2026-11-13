# Routing toward a 100 ms request budget

The product target is a visible route update within one second for runs up to
40 km after a settings change or pin release. See the
[interaction target](routing-scalability.md#interaction-target) for scope and
unresolved measurement conditions. The 100 ms backend budget below is a supporting
engineering goal. Neither target has been established for general use.
The [glossary](glossary.md) explains search, storage, and measurement terms.

Run planning and distance exploration use Rust search when a matching numeric
routing file is available. Python still snaps locations to paths, manages request
deadlines, and retries with larger search areas. The graph caches have byte
budgets and remove older entries when space is needed. Overlapping areas share
validated chunks, but still assemble some indexes and preference lookups for each
area. Exploration snapshots keep only reached-node distances and metadata.
See the [numeric format and benchmark commands](numeric-routing-format.md).

The measurements below describe particular datasets and code versions. Read each
with its cache state and measurement boundaries; earlier results do not measure
later architectural changes.

The [2026-11-12 edit measurements](archive/history/2026-11-12-shared-store-measurements.md)
exercise changed preferences and endpoints after shared chunk reuse. A 38.54 km
shaped request took 1.42 s median across three repeated edit sequences at the
default cache size. A 1 GiB control removes repeated topology loading but still
leaves area assembly and signal-data work on endpoint edits. These are backend
observations from before conditional input loading. With environment data
configured but its preferences Off, default-cache requests repeatedly reached the
deadline. A fully cached 39.63 km route with mixed preferences took 2.22 s, about
2.10 s of it planning. Both preparation
and search need improvements; the one-second visible-update target remains
unverified.

The [conditional-input follow-up](archive/history/2026-11-12-conditional-routing-inputs.md)
produces complete responses with the same hashes as before. It avoids loading
environment and signal data for the whole area when their preferences are Off. At the default budget, the
configured-environment 38.54 km case completed at a 1.05 s repeated median;
peak RSS for that workload fell from 2,475 to 762 MiB.
The [November 13 packed-input measurements](archive/history/2026-11-13-packed-routing-inputs.md)
cover the optional hill/environment/signal artifact, reduced mixed-preference
loading, and its original hill-only regression. The
[compact-storage follow-up](archive/history/2026-11-13-compact-hill-inputs.md)
restores performance for hill edits using cached data. First activation of the
hill channel and broader deployment checks remain outstanding. Assembling each
area and searching it still need improvement. Browser measurements are also
needed to assess the visible-update target.

For Rust phase timing, allocation traffic, and visual results on a fixed working
area, see the [2026-11-02 native profile](archive/history/2026-11-02-native-routing-profile.md).
It separates normal-build timings from instrumentation overhead and does not
include the complete API request.

The [2026-11-10 hot-loop comparison](archive/history/2026-11-10-native-search-hot-loop.md)
measures early edge rejection, scalar objectives, full-geometry handling, edge
passing, and predecessor storage separately on a fixed working area. Its timings
exclude graph preparation, API window expansion, rendering, and serialization.

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
could not retain both expanded graphs in that measurement. Shared chunk reuse is
now implemented; these historical timings do not measure that change.

Reproduce with `benchmark_run_latency` and the numeric sibling artifact present:

```sh
uv run python -m runmap.experiments.benchmark_run_latency \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --hills high --repeats 10 \
  --report artifacts/pathfinding-profile/native-api-high.json
```


For the proposed next steps on loading, landmark bounds, and larger coverage,
see [routing scalability](routing-scalability.md), which distinguishes delivered
chunk reuse from the remaining proposals.

## Longer-run validation

The [2026-11-13 compact-storage follow-up](archive/history/2026-11-13-compact-hill-inputs.md)
measures the later 40 km workload after removing duplicated hill storage and
restoring packed-hill edit performance. It does not establish browser acceptance
of the one-second target. The observations below retain their original context.

The API segment cap is now 650,000, with five retries, a 2,500 km² window
limit, and the existing 15-second deadline. A request from `(24.795, 60.216)`
to `(24.96, 60.25)` completed after windows of 96,953, 303,738, and 550,897
segments, with hills off and default shape settings. The two candidate runs
were each 29.87 km. Hills high produced 30.55 km and 31.02 km candidates.

| Preference | First request | Three repeated requests, median | Peak process RSS |
| --- | ---: | ---: | ---: |
| Hills off | 4.00 s | 3.01 s | 0.93 GB |
| Hills high | 5.76 s | 3.30 s | 0.95 GB |

These single-worker observations include JSON serialization, exclude HTTP
transport, and have uncontrolled OS cache state. Results were deterministic
across repeats. Numeric preparation took about 1.5–1.8 seconds and searches
about 0.4–0.6 seconds across all attempts in representative repeated samples;
other time includes segment-count checks and rendering. The 128 MiB graph
cache cannot retain these large working sets, so repeated requests reload them.
These long runs complete, but do not meet the 100 ms target in this measurement.

Reproduce the request and record candidate lengths with:

```sh
uv run python -m runmap.experiments.benchmark_run_latency \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --turnaround 24.96 60.25 --max-segments 650000 --hills high --repeats 3 \
  --report artifacts/pathfinding-profile/long-runs/30km-high.json
```

The benchmark's `--max-segments` and `--max-retries` flags override only that
benchmark. The server uses its defaults or the `RUNMAP_RUN_MAX_SEGMENTS` and
`RUNMAP_RUN_MAX_RETRIES` environment overrides.

## Native distance-exploration integration

Snapshot preparation and start-distance inspection use numeric exploration when
a matching artifact is present. The measurements below predate distance shells:
viewport highlighting and snapshot path tiles have since been retired in favor
of [grid and filtered-triangulation shells](distance-shells.md).
The source database and version-2 numeric artifact need no format migration.
Snapshot outputs retain only immutable reached-node IDs/distances and metadata,
so a graph-cache eviction or shutdown does not invalidate them. The measurements
below used the then-current 50,000-segment cap, two retries, 2,500 km² area cap,
and preparation deadline. See [current settings](development.md#configuration).

At Leppävaara start `(24.795, 60.216)`, fresh snapshot preparations reusing native
graphs had these observations (five repeated samples, no snapshot-cache hits):

| Forward budget | First preparation | Repeated preparation median | Native search median | Snapshot bytes |
| --- | ---: | ---: | ---: | ---: |
| 0.5 km | 91 ms | 2.5 ms | 0.11 ms | 9,788 |
| 1 km | 41 ms | 4.6 ms | 0.38 ms | 39,100 |
| 2 km | 195 ms | 10 ms | 1.22 ms | 135,836 |

OS caches were uncontrolled, and the samples ran sequentially in one worker.
These are snapshot-preparation timings, excluding map delivery/encoding and HTTP
transport. A separate real-data comparison across 1,501 displayed intervals at
1 km matched all geometry, inclusion states, directional minima, and point
distances exactly. Native snapshot ownership was about 39 KB versus 6.7 MB for
the reference snapshot. That comparison's combined snapshot and viewport times
were 149 ms and 458 ms, respectively (single observations).

A larger 27,487-interval GeoJSON viewport produced roughly 21–22 MB of JSON and
took 0.6–1.4 seconds to generate in the same probe. Fast search does not establish
a corresponding latency bound for arbitrarily large display output. The normal
shell visualization now avoids shipping every reached path interval.

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

The storage and search design below is mostly implemented. The
[architecture](architecture.md#routing-backend-boundaries) describes current
ownership and loading; the [roadmap](roadmap.md#next-routing-responsiveness-and-scaling)
lists remaining work.

Prepare routing data before serving requests, in chunks that can be read
individually. The data needs:

- Stable integer IDs for nodes, segments, and directed arcs.
- Connection indexes within chunks and references across chunk boundaries. Keep
  unloaded connections distinct from physical dead ends and missing source data.
- Projected coordinates and segment geometry.
- Access flags for each supported policy, including node restrictions and prepared
  boundary rules.
- Directional hill totals for complete segments, with separate detail for partial
  traversals. Topology format 3 keeps hills in the paired input file.
- Bounds and metadata identifying the dataset, file format, and preparation models.

Binary formats must specify number types, byte order, array shapes, lengths, and
integrity checks. Aligned uncompressed arrays can support read-only mapping, but
the current SQLite path reads payload bytes into memory. Compressed inputs need
bounded decompression and a decoded-data cache; include this work in first-load
measurements. Keep descriptive metadata separate for route display and inspection.

Rust traverses the arrays directly, avoiding a Python call or new Python record
for every visited arc. The Python planner remains a correctness reference. Each
request keeps its own temporary endpoint connections, shape calculations, and
search state. Shared chunks remain read-only. Calculate shape costs as needed and
reuse them between outbound and return searches. Complete-segment hill costs use
totals; partial endpoint connections use the corresponding intervals. Preserve
one-way rules, access policies, cost formulas, boundary reporting, and deadlines.

Chunk-local indexes and explicit connections should reduce repeated assembly.
Currently, each area still needs merged indexes and selection records. It must
not rebuild Python objects for every edge. Reuse loaded chunks between window
retries, limit cached bytes, and include dataset and feature versions in cache
identities. Keep buffers alive while any search uses them, even after eviction.
Avoid loading the whole dataset or using an unlimited object cache.

Distance exploration uses the same chunks and Rust search. Its result buffers
have separate byte limits and remain valid independently of the graph cache.
More reachable data still means more output work.

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
peak RSS, and tracked allocations. State which areas, route lengths, and response
sizes the measurements cover. A strict 100 ms bound for arbitrary distances,
physical cold storage, or arbitrarily large exploration output cannot be inferred from
fast local queries. If native search itself exceeds budget, evaluate spatial or
hierarchical acceleration with valid lower bounds for request-specific shape and
reuse costs. Shortcuts based only on physical distance are not automatically
valid for the planner's changing costs.

## Implementation sequence

The end-to-end benchmark, versioned numeric export, Rust traversal, bounded chunk
reuse, and shared distance-exploration path are implemented. Area expansion still
retries searches and assembles indexes for each new window. Loading chunks as the
search needs them remains proposed work.

Next, run the acceptance cases above and report which workloads meet the 100 ms
target. Follow the [roadmap](roadmap.md#next-routing-responsiveness-and-scaling)
for outstanding implementation and validation work.

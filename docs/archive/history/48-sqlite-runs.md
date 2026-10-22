# SQLite run planning

Run planning now queries complete local topology and saved directional hill
features from SQLite, with no raw measurement-array load or source JSON read.
The lazy global-JSON bridge and its retained `PathData` ownership have been
removed. All path endpoints work from the selected database alone. Deployments
without a database still use the existing JSON loader; export/select a database
to enable this runtime path. No database is built automatically during startup.

## Area and request behavior

The initial EPSG:3067 box encloses the snapped endpoints and expands by 50% in
total width and height (25% per side), floored to 1,000 m in each dimension.
The viewport has no effect on legal routes. All physical segments, endpoint
records, directed arcs, original chainages, policies, and hill features in the
selected window retain stable IDs and share local dense indices.

Search reports visited frontier nodes, including a destination popped from the
queue. A reached frontier, candidate geometry near/outside the query box, or an
incomplete assignment with unloaded connections triggers a new attempt with
double width and height. Proximity means within the larger of 100 m and 5% of
the shorter dimension. Both candidate side assignments and return legs are
recomputed in each expanded graph; legs from different attempts are never mixed.

Defaults allow three retries, 2,500 km² query area, and 50,000 selected segments.
Area and bounded count checks happen before decoding. One deadline covers both
endpoint snaps, lease waiting, SQL, local loading, cost preparation, and all
searches/retries. Two planner slots bound concurrent requests. The local graph
and hill arrays are released after response construction; there is no retained
planner graph cache. Completed candidates from a deadline-interrupted attempt
can be returned under `status=deadline`, matching the reference behavior.

`RunResponse.search` records attempted projected boxes, node/segment counts,
retry reasons, frontier and candidate-boundary involvement, and source coverage
flags. Exhausted limits return `status=area_limited` with an explicit reason and
no provisional candidate legs. This is separate from `unreachable`,
`no_eligible_snap`, and source-coverage limitations. The UI has an explicit
area-limit message. Expansion is heuristic; a better route outside the queried
area can remain undetected. Full-dataset optimality is not promised.

Preference capabilities/configuration use global prepared coverage fractions,
so a window does not change advertised preference support or weights. Actual
traversal observations still describe the selected route. Hill integration uses
the original graph chainages, including reverse and partial endpoint traversals.

## Validation

Eleven new planner tests cover all policies, hills off/low/high, zero/default
shape strength, partial endpoints, identical endpoints, missing snaps, unsupported
preferences, one-way/stair/node access, genuine unreachable components, source
edges, forced detours, complete bent segments outside a query box, area/count/
retry limits, one deadline across expansions, deadline partial candidates,
planner slot rejection/release, and source-free endpoint/config behavior.
Parity tests forbid reading source JSON and loading raw local measurement arrays.

The complete Python suite passes: 289 tests. The frontend production build and
three transport tests pass. Existing JSON/reference behavior remains available
for comparison and debugging.

## Saved routes and reader resources

```sh
uv run --extra web python -m runmap.experiments.benchmark_sqlite_runs \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  --report docs/archive/history/48-sqlite-runs.json
```

The source contains 55,627 nodes, 69,707 segments, and 278,839 measurement
intervals. The probe uses three saved route requests, each with hills off/high.
All six complete, returning two candidates each. Exact response hashes match
the full-graph reference for status, snapped endpoints, candidate geometry,
traversals, costs, observations, and configuration. Hashes exclude timings,
expanded-node counts, and local-area metadata.

| Request | SQLite hills off/high | JSON hills off/high | SQLite retries | Final selected segments |
| --- | ---: | ---: | ---: | ---: |
| Initial outing | 5.529 / 5.670 s | 0.637 / 0.336 s | 2 | 34,688 |
| School directional hill | 0.167 / 0.168 s | 0.017 / 0.034 s | 0 | 2,383 |
| Bridge to underpass | 0.166 / 0.183 s | 0.020 / 0.028 s | 0 | 2,414 |

SQLite initialization takes 3.52 ms; JSON initialization takes 9,476 ms. Peak
reader RSS across these six requests is 612.7 MiB for SQLite versus 827.8 MiB
for JSON. SQLite reloads local graphs/hills on each request and expansion; the
reference retains its globally prepared graph after initialization. Thus local
loading improves startup and area-dependent memory but adds per-request loading
and retry latency. The larger route visits frontiers in its first two windows;
the settled third window contains 27,328 nodes and 34,688 segments. The 1,000 m
minimum fits the saved short/partial-segment routes without retrying.

Readers run in separate fresh subprocesses after offline export, with warm OS
file cache. Peak RSS includes Python/library imports, temporary JSON/feature
decoding, SQLite cache, and request working data; it is not retained graph size.
The probe does not measure cold disk, HTTP/browser latency, concurrent requests,
latency percentiles, or global optimality across arbitrary routes. Larger
capital-area cases and deployment limits still need representative measurements.
Temporary hill/feature decoding remains an optimization opportunity.

Raw measurements, requests, attempt traces, and comparisons are in
[48-sqlite-runs.json](48-sqlite-runs.json). Current setup and limitations are in
[development](../../development.md#export-a-sqlite-network-dataset),
[architecture](../../architecture.md#sqlite-storage-foundation), and
[roadmap](../../roadmap.md#next-sqlite-storage-and-bounded-network-loading).

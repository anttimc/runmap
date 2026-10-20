# Python A* with a prepared integer index

Measured 2026-10-20 on macOS 26.6.2 ARM64, CPython 3.14.6. Search remains in
Python; no further Rust migration is included. Current behavior is described in
[architecture](../../architecture.md) and reproduction commands in
[development](../../development.md).

## Change

Prepare dense node, physical-segment, and directed-arc indices alongside the
existing string identifiers. A* uses lists for distances and predecessor arc
indices and follows prepared adjacency records containing integer destinations.
Blocked segment IDs are converted once before searching. The existing cost and
heuristic callbacks retain their contracts; shape/hill feature caches remain
string-keyed. Selected paths return the existing external string arc IDs.

Heap entries retain the string node name solely for the same tie ordering as the
previous implementation. Consequently equal-cost alternatives are not selected
by arbitrary integer assignment order. The algorithm still supports reopening
nodes for admissible but inconsistent heuristics, directed policies, request
blocks, generic costs, and cooperative deadlines.

Policy indexes share node/segment/arc mappings and records, rebuilding eligible
adjacency. Endpoint overlays share the full base arrays, append only their small
set of extra IDs/records, and override affected rows. Initial experiments that
copied full pointer arrays into each overlay regressed the short-route benchmark;
the retained implementation avoids these per-request copies. Search arrays are
fresh per invocation, avoiding shared mutable search state between requests.

The bounded exploration Dijkstra is unchanged. Its temporary start overlay also
has a consistent integer view so it remains usable by A*, but this report does
not claim an exploration speedup.

## Method

The baseline is the pre-change string-indexed A* retained in
`experiments/search_reference.py`. The benchmark patches only planner search and
skips preparing the unused integer overlay for the baseline. Patch setup is
outside timed intervals. The integer path includes its actual overlay preparation.
The same current planner, dataset, cost model, and integrator are used on both
sides. This measures the combined prepared layout and list-based bookkeeping;
it does not isolate integer hashing alone.

Twenty timed requests follow a warm-up, alternating implementation order. Each
request creates fresh feature caches. Loading, policy/index preparation, snapping,
HTTP delivery, and rendering are excluded from route timings. The separate
length-only searches use an already constructed overlay, the same geometric
heuristic, and an arc-length callback, excluding integration and planner assembly.
Their timings include fresh distance/predecessor list allocation.

The graph is `leppavaara-flat-10k/graph.json`, with SHA-256
`6a8b61db10ffc7650b26ff4757583a6c3ea3c92cd516ef882188265febf295de`:
55,627 nodes, 69,707 segments, and 139,414 directed arcs. Base endpoints are
(24.795, 60.216) and (24.815, 60.225), default `RunSettings`, hills off. The
[retained JSON](34-integer-search-index.json) records all scenario endpoints,
settings, median/p95 timings, preparation measurements, and equivalence checks.

## Results

These full planner timings use the existing release-built Rust integrator in
both implementations, including its Python wrapper and conversion overhead:

| Scenario | String median | Integer median | Reduction |
| --- | ---: | ---: | ---: |
| Shorter route | 26.42 ms | 22.41 ms | 15.2% |
| Existing profile | 52.11 ms | 43.08 ms | 17.3% |
| Longer route | 123.34 ms | 100.10 ms | 18.8% |
| Moved start | 51.18 ms | 43.07 ms | 15.9% |
| Narrow centre field | 43.84 ms | 36.69 ms | 16.3% |
| Shape off | 3.89 ms | 3.36 ms | 13.6% |

With the Python integrator explicitly selected for both sides, the profile route
went from 76.04 ms to 66.68 ms, a 12.3% reduction. This separates the integer-index
benefit from the previous Rust integration improvement.

The profile's isolated length-only A* search went from 1.56 ms to 1.01 ms;
the longer route's search went from 6.71 ms to 3.91 ms. These are approximately
35% and 42% reductions, respectively. Full planner speedups are smaller because
cost evaluation, integration, response assembly, and other work remain.

All scenarios produced matching outbound/return arc sequences, costs within
relative tolerance 1e-12 and absolute tolerance 1e-9, and identical expansion
counts. The Python suite passed 206 tests, including generated directed graphs,
policy changes, lexical heap ties, overlay isolation, and deadlines.

## Cost and limits

Cold construction of a fresh integer index for the selected policy took a median
161 ms over three passes. A separate `tracemalloc` construction retained about
41.5 MB of additional allocations (39.6 MiB), with a 42.0 MB peak. This counts
the new records, mappings, and adjacency, excluding the already loaded raw graph;
it is not process RSS. Additional policy views share topology but allocate their
own adjacency. Preparation and memory are costs of this change, paid outside
ordinary route interactions.

The implementation still allocates graph-sized distance/predecessor lists for
every A* search. This was included in timings and did not regress the tested
shape-off or shorter routes, but much smaller searches or larger extracts could
change the tradeoff. p95 values from twenty samples are limited evidence. Hills,
concurrent planners, API/browser latency, and other datasets need separate checks.
The measurements support the Python layout change, not a claim of instant
end-to-end interaction or an estimate of a future Rust search's performance.

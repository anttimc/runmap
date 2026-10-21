# Indexed distance exploration — 2026-10-21

This increment moves bounded distance search onto the existing prepared integer
adjacency. It replaces the retained string-keyed distance dictionaries and their
per-snapshot copies with shared, immutable native numeric buffers. The planned
measurement/geometry packing and persistent startup artifacts remain in the
[roadmap](../../roadmap.md#next-shared-integer-indexing-and-compact-prepared-data).
Current behavior belongs in [architecture](../../architecture.md).

## Representation and compatibility

The working search arrays use Python's standard-library `array`: float64 values
by node index and int64 node indices in first-discovery order. Publication copies
them into immutable byte owners exposed through read-only memoryviews. Search
caches and all snapshots reusing that search retain the same result object;
snapshot creation does not copy either buffer. Unknown distances are infinity.

The original base node-ID tuple is shared across policy/endpoint overlays and
used as an ordering guard. Extra node IDs identify the temporary snapped start.
Point queries use integer endpoint lookups, retaining the virtual start's
zero-distance anchor and all existing directional inclusion rules. The legacy
`shortest_distances` function adapts its string inputs and dictionary output
around the integer search. The former string search remains an explicit
experimental reference. No routing eligibility, grade/distance bins, serialized
measurement format, tile version, or planner cost model changes.

Payload is `8 N + 8 R` bytes for N graph/overlay nodes and R reached nodes, plus
small container overhead. This trades a predictable eight-byte-per-node floor
for direct lookups and reduced large-budget object overhead. Very small searches
can consume more memory than the old dictionaries. Snapshot accounting charges
each entry the full buffer size even when another cache entry shares it.

## Validation and isolated measurements

All 220 Python tests pass, covering generated forward/reverse searches with
blocks, exact external discovery order, invalid starts, deadlines, absent JSON
records/string adjacency, immutable buffers, graph-order guards, snapshot sharing,
cache lifetimes, direction restrictions, closed ways, and partial-edge inclusion.

Compared with commit `84fa7a3` on `leppavaara-flat-10k`, dataset identity
`b74d86e5dcebdb20802ec502ed024214d8950f46fa2b0d7c9c589188d028d81d`.
Python 3.14.6 on macOS ARM64. Both implementations use the same graph, policy,
snap, and endpoint-overlay preparation. Ten paired timings alternate order with
fresh search results, excluding allocation tracing. Separate tracemalloc runs
measure retained search plus one snapshot after garbage collection, excluding
the already-loaded shared dataset. This is not whole-process memory.

| Budget | Reached nodes | Before median ms | Indexed median ms | Before retained bytes | Indexed retained bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 km | 2,251 | 2.70 | 1.19 | 158,464 | 464,274 |
| 3 km | 16,335 | 23.54 | 10.16 | 1,222,608 | 576,722 |
| 10 km | 51,901 | 77.89 | 33.61 | 5,090,824 | 861,210 |
| 30 km | 52,014 | 77.84 | 33.53 | 5,093,536 | 862,114 |

All searches had 55,628 nodes including the temporary start. The new search is
approximately 2.3 times faster here, with an approximately 83% retained-memory
reduction in the larger cases and increased memory at 1 km.

Four thousand sampled interval-inclusion/midpoint-distance comparisons matched
exactly. All 48 encoded tile comparisons were byte-identical: All/Surfaced and
stair policies, 3/10 km budgets, zooms 13/15, two locations, gradient/distance
variants. Detailed observations are in the [comparison JSON](37-indexed-exploration.json).

## Capital-area integration check

The live capital-area backend reported ready with its unchanged dataset identity.
All 3/10/30/50 km snapshot requests succeeded. Accounted snapshot sizes were
4.20/5.38/7.48/7.59 MB. The first request took 3.85 s including cold policy setup;
subsequent requests took approximately 198/340/329 ms. These are single local HTTP
observations with shared-machine activity, not isolated latency targets or proof
of browser responsiveness. The per-node floor matters more on this larger graph.
See the [live observations](37-indexed-exploration-live.json).

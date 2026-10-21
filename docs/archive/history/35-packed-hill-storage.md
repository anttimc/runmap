# Packed hill storage — 2026-10-21

The first shared-index migration binds hill storage to the existing integer
graph's segment ordering. It removes the retained string-keyed mapping of
per-interval `HillInterval` and directional `HillFeatures` objects, replacing it
with read-only numeric arrays. Existing serialized artifacts and model identifiers
are unchanged. Current behavior belongs in [architecture](../../architecture.md);
remaining migration steps are in the [roadmap](../../roadmap.md#next-shared-integer-indexing-and-compact-prepared-data).

## Representation

For S physical segments and I measurement intervals (including fallback intervals):

| Array | Shape | Type |
| --- | --- | --- |
| Interval offsets | S + 1 | int64 |
| Graph chainage | I × 2 | float64 |
| Interval features | I × 2 directions × 7 features | float64 |
| Segment totals | S × 2 directions × 7 features | float64 |

Array payload is `128 I + 120 S + 8` bytes. The segment ID mapping is shared
with the prepared integer graph rather than constructed again for hills.
Whole traversals use stored totals. Partial traversals search interval bounds,
preserving the original summation and partial-scaling operations. Temporary
endpoint segments resolve to the original physical segment and chainage span.

## Local evidence

Compared against the implementation committed as
`9df3dbf` on the local `leppavaara-flat-10k` dataset, graph identity
`b74d86e5dcebdb20802ec502ed024214d8950f46fa2b0d7c9c589188d028d81d`.
Python 3.14.6 on macOS ARM64. The comparison executes the committed hill module
with its committed data types as a reference, using the same current graph,
measurements, search, and shape integrator on both sides.

| Measurement | Object representation | Packed arrays |
| --- | ---: | ---: |
| Retained allocations during hill construction | 110,649,304 bytes | 44,057,604 bytes |
| Packed array payload | — | 44,056,240 bytes |
| Peak traced allocations during construction | 115,192,808 bytes | 112,238,430 bytes |
| Construction time with allocation tracing enabled | 7.69 s | 9.22 s |

Retained hill allocations fell by approximately 60%. Tracemalloc starts after
loading the shared dataset, so these figures exclude existing graph, measurement,
display, and routing allocations. They are not whole-process memory measurements.
The peak includes validation/serialization temporaries. Construction became slower
under tracing; this does not establish a startup improvement.

Using seed 42, 1,000 sampled segments were compared in both directions, for full
and 17–83% traversals: all 4,000 results matched exactly across all seven fields.
Twelve planner cases used two endpoint pairs, zero/default shape width, and
Off/Low/High hill settings. Candidate paths, costs, observations, traversals,
expanded-node counts, and outcomes matched exactly. The reference lookup was
injected into the same planner to isolate storage/evaluation changes. Single-run
planner timings are diagnostic and do not establish a latency distribution.

The Python suite passes 214 tests, including randomized partial-span equivalence,
read-only arrays, original segment ordering despite reordered source records,
identity guards, policy sharing, invalid spans, full-segment direct lookup, and
deadline checks. Detailed results are in the
[comparison JSON](35-packed-hill-storage.json).

After reload, the full capital-area backend reported ready with the same dataset
identity. macOS vmmap measured a 7.3 GiB physical footprint (8.4 GiB peak), versus
the preceding 8.9 GiB observation (10.3 GiB peak). These process observations have
different cache/request histories; the isolated allocation comparison above is
the evidence for the hill storage reduction. Most backend memory was still
compressed or swapped, so this is not proof of overall browser responsiveness.
An initial capital-area HTTP smoke attempt timed out. Subsequent checks returned
ready configuration in 159 ms and completed the same local route with Low in
411 ms and High in 306 ms (server-reported times, shape disabled). These warmed
checks confirm live integration, not a cold-response or startup latency target.

# Packed measurement storage — 2026-10-21

This increment releases the loaded measurement dictionary tree after packing it
into read-only arrays in the existing integer graph's physical-segment order.
Display intervals retain integer segment/local-interval references rather than
measurement dictionaries. The [roadmap](../../roadmap.md) retains the remaining
geometry/display packing, policy topology sharing, and persistent startup work.

## Representation and compatibility

`MeasurementData` shares the graph's segment ID tuple and lookup, with int64
segment offsets locating sample ranges. Six float64 columns store projected
start/end, graph start/end, signed grade, and height change. Two presence masks
preserve independent grade/height nullability, and a separate validity mask
preserves sample validity. A uint32 code points to pooled, ordered quality-flag
tuples; unknown and duplicate flags survive round trips. Coordinates are packed
as float64 pairs with int64 interval offsets, preserving the stored vertices
exactly rather than recomputing them from graph geometry.

Inspection materializes one segment's records. Hill preparation accepts this
store and reconstructs one segment at a time, including the existing fallback
for empty measurements. Graph identity and shared ordering guard hill input.
Viewport and snap requests materialize their candidate records. Tile generation
reads the numeric columns through local native memoryviews and builds only the
location fields needed for exploration state. This avoids NumPy scalar/view
allocation and full measurement reconstruction in the tile loop.

No published measurement schema, route cost model, eligibility rule, tile version,
grade bin, flat threshold, or inspection behavior changes. No legacy document
property or retained second dictionary representation remains in the service.
The experimental hill benchmark was migrated to segment reads as well.

## Validation and measurements

Reference: `b71aa47`. Dataset: `leppavaara-flat-10k`, identity
`b74d86e5dcebdb20802ec502ed024214d8950f46fa2b0d7c9c589188d028d81d`.
It contains 69,707 physical segments and 278,839 measurement intervals. Runs used
Python 3.14.6 on macOS 26.6.2 arm64. Full observations and output hashes are in
[the benchmark data](38-packed-measurements.json).

| Observation | Reference | Packed |
| --- | ---: | ---: |
| Retained measurement + display containers | 339,097,845 B | 49,637,820 B |
| One standalone dataset load | 7,663 ms | 7,471 ms |
| Process peak RSS observed after load | 1,212,235,776 B | 1,278,328,832 B |

Retained subsystem storage fell **85.36%**, or 289 MB. Recursive
`sys.getsizeof` accounting uses one shared visited set across measurements and
display features, includes owned array payloads, and excludes the already-owned
graph and shared integer segment ID tuple/map. This is not a whole-process
memory figure. The peak RSS samples do not establish a reduction in startup peak
memory; JSON parsing, temporary dictionary allocation, and allocator retention
remain. Loading constructs routing indexes before packing so the IDs are shared.

Equivalence checks passed for every reconstructed measurement, all four hill
arrays, 400 full/reverse/partial inspections across 100 sampled segments, two
viewport responses, and 48 byte-identical exploration tiles. The tile matrix
covers two locations, zooms 13/15, gradient/distance variants, 3/10 km budgets,
and all/surfaced/stair policies. JSON comparison hashes normalize integer versus
float spelling while preserving numeric values, nulls, ordering, and coordinates.
Tile comparisons hash the encoded bytes directly.

Final direct tile-encoding medians were 6–9% lower in eight sampled cases, with
five uncached encodes per case after warmup. These were separate process runs,
not an interleaved controlled comparison; concurrent backend reload activity
and system memory pressure were not controlled. Treat latency as a smoke check,
not a demonstrated application speedup. An earlier complete-record tile reader
showed overhead and was replaced with the direct column reader before the final
run. Startup time is approximately unchanged in these single observations.

All **223 tests passed**. New tests cover independent nulls, unknown/duplicate
flags, detached input/output records, read-only arrays, invalid interval identities
and indices, shared graph IDs, hill equality, and graph-ordering rejection. Existing
tile seam, policy, inspection, flat-run, snapping, routing, and HTTP tests pass.

## Remaining work

The backend was stopped by the user during development because source reloads
repeated expensive startup. It was left stopped; no completed live capital-area
or browser acceptance check is claimed. The standalone dataset comparison and
tests do not require a running server.

Raw graph/projected geometry maps, per-interval display objects, spatial cell
lists, and policy adjacency duplication remain. Interval coordinates are compact
but still independent of segment geometry to preserve sampled positions exactly.
Persisting prepared arrays is the next startup-focused step: this change still
parses the entire measurement JSON and temporarily holds its dictionary tree.

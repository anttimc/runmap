# Packed node and segment metadata — 2026-10-22

This increment removes retained node/segment dictionaries and tag dictionaries
from the prepared graph. Numeric columns, pooled strings, and on-demand public
views share the integer graph's ID tuples/maps and packed WGS84 geometry.
The API's segment lookup reuses the routing lookup rather than materializing a
second dictionary of every segment.

Node coordinates use float64 plus integer-spelling flags. Sparse optional strings
and ordered segment tags use pooled strings, int64 row offsets, and uint32
key/value codes. Segment endpoints use int64 node indices; numeric metrics use
float64, sample counts use int64, and separate presence/boolean/spelling flags
preserve missing fields, zeroes, booleans, and integer versus float JSON spelling.
Sample counts above 2^53 remain exact. Way IDs are pooled without changing their
external values. Unknown metadata fields fail explicitly instead of disappearing;
unknown string tags remain supported without normalization.

`NodeRecords`, `SegmentRecords`, and `MetadataLookup` reconstruct detached records
only when accessed, retaining no record cache. Segment records return read-only
coordinate views, with detached coordinates on indexing. Deep copies materialize
editable metadata lists and tag dictionaries while sharing read-only geometry.
Consumers changing source tags must use editable source records or explicit
overrides and then rebuild; mutating a returned public record does not change
the prepared base. Existing tests that edited prepared records now explicitly
materialize editable fixtures before modification, preserving their assertions.

Canonical graph hashing materializes nodes, segments, and arcs in batches of
1,024, preserving the original checksum algorithm without rebuilding the complete
dictionary tree. Full export still materializes records at its JSON boundary.
Static tile eligibility reads each candidate segment once, avoiding repeated
reconstruction of its metadata.

## Capital-area retained memory

Reference: `0ce9daa`. Dataset identity remains
`fda8c40d3e79bbd078a7edcdc485fd7d45a5bd685fe05c0db32e9fd15567039f`,
with 508,727 nodes, 631,979 physical segments, and 1,263,958 directed arcs.
Measurements use standalone graph loads without starting the backend.

| Retained graph data | Reference bytes | Packed bytes |
| --- | ---: | ---: |
| Graph document and referenced prepared data | 1,115,816,002 | 352,892,253 |
| Additional projected geometry | 40,847,008 | 40,847,008 |
| Additional integer graph | 166,594,827 | 166,601,883 |
| Other base routing indexes | 66,753,838 | 61,913,621 |
| **Total graph + base routing** | **1,390,011,675** | **622,254,765** |

The reduction is **767,756,910 bytes / 55.2%**. Approximately **622 MB / 593 MiB**
of graph/base routing data remains. Recursive `sys.getsizeof` uses one visited
set, counting immutable buffer owners and owned NumPy payloads once. Dataclass
views are inspected through stored fields without reconstructing public records.
Attribution across rows depends on traversal order; the total is independent of
that ordering. Measurements, hills, display data, extra policies, request caches,
interpreter overhead, and allocator retention are excluded. This is not process RSS.

On `leppavaara-flat-10k`, graph/base routing storage falls from 154,130,249 to
69,140,236 bytes. Complete initial `PathData` storage, including measurements,
hills, display metadata, and spatial indexes before interaction caches, falls
from 236,889,248 to 149,976,755 bytes (**36.7%**).

## Equivalence and timing

The current suite passes **253 tests**, including the concurrently developed
SQLite prepared-feature tests. New checks cover canonical identity across the
checksum batching boundary, optional-field presence and numeric spelling,
large integer counts, source/output detachment, read-only buffers, pooled ordered
tags, shared policy/overlay ownership, empty graphs, export, unsupported fields,
and slicing/negative indices. Existing generated routing/search checks remain.

Real-data comparison against `0ce9daa` preserves:

- Every reconstructed measurement record (278,839) and all four hill arrays.
- 100 metadata inspections plus 400 full/reversed/partial measurement inspections.
- Four viewport responses and 48 byte-identical static/exploration tiles, covering
  two locations, zooms 13/15, all/surfaced policies, hidden/all static variants,
  gradient/distance exploration, and 3/10 km budgets.
- Eighteen planner cases across three turnaround locations, three avoidance
  widths, and hills off/high. Paths, costs, expansion counts, traversals,
  observations, and coordinates match.
- Twelve bounded-search cases across both policies, 1/10/30 km budgets, and
  forward/reverse search. Distance values and discovery ordering match exactly.

Tests and measurements use Python 3.14.6 on macOS 26.6.2 arm64. Reference and
packed versions run in separate processes with the same native integration
binary. The reference runs first; system activity is not controlled. Five
uncached static-tile samples per case have a median paired ratio of **1.049**
(range 0.943–1.097): rebuilding metadata has a small tile-time cost. Bounded search
uses the unchanged numeric buffers directly; observed median timing ratios range
0.979–1.176, with a median of 1.084. Single planner samples have a median ratio
of 0.992 (range 0.867–1.044). These are diagnostic observations, not controlled
latency distributions or application speedup claims.

Single load samples rise from 6.69 to 8.28 seconds for the smaller dataset and
11.65 to 15.14 seconds for the capital-area graph. Packing adds preparation work;
no startup speedup is claimed. Peak RSS observations are included in the raw
report but cover loading and benchmark work, not retained subsystem memory.
No live-server or browser acceptance check was performed.

Raw signatures, timings, and memory observations are in
[the benchmark data](43-packed-metadata.json). The tracked benchmark can run
against a separate checkout via `PYTHONPATH`, using the same native binary:

```sh
uv run --offline --no-sync python -m runmap.experiments.benchmark_metadata \
  data/paths/prepared/leppavaara-flat-10k/graph.json /tmp/metadata-small.json
uv run --offline --no-sync python -m runmap.experiments.benchmark_metadata \
  data/paths/prepared/helsinki-capital-area/graph.json /tmp/metadata-capital.json \
  --graph-only
```

This work does not modify SQLite tables, readers, preparation, or selective API
delivery. Shared dense indices remain local to their prepared graph, so the same
containers can pack a loaded database window. The [roadmap](../../roadmap.md)
retains interval/segment geometry sharing as an evaluation and filtered adjacency
as a measured tradeoff. SQLite integration remains separate work.

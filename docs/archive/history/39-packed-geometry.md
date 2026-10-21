# Packed graph geometry — 2026-10-21

This increment packs the static graph's WGS84 segment geometry and projected
node/segment coordinates. It removes retained coordinate lists, projected
point tuples, and projected string maps. The existing integer graph supplies
the node/segment ordering. Remaining metadata, adjacency, display packing, and
persistent startup artifacts stay in the [roadmap](../../roadmap.md).

## Representation and behavior

`PackedLines` contains read-only float64 coordinate-pair buffers with int64
segment offsets. WGS84 and projected segment buffers share their offsets;
projected nodes use a separate float64 pair buffer. `ProjectedGeometry` shares
the integer graph's node/segment ID tuples without retaining another ID lookup.
Policies reuse the prepared geometry. Endpoint overlays share all base buffers
and retain only their few added projected points and lines.

Each segment record holds a small `PackedLine` read-only sequence view identifying
its segment range. This behavioral class is an array view, not a service or
operation namespace: indexing, slicing, iteration, and reversed traversal preserve
existing geometry consumers without retaining Python coordinate lists. Returning
coordinates creates detached records. Metadata deep copies share the read-only
view; it does not copy the network or attempt to pickle a memoryview.

WGS84 coordinates retain their exact numeric values. Optional per-coordinate
integer masks also preserve integer versus float JSON spelling, so packing does
not change published graph checksums. The graph hashing/artifact serializers
materialize one line at a time through an explicit encoder. Segment inspection
returns ordinary coordinate lists. Route coordinates are detached lists as well.
Coverage envelope calculation streams coordinates, avoiding a temporary list
containing all expanded segment vertices.

Base-index construction replaces the input segment coordinate containers with
these views. Mutating the previous coordinate lists or an emitted coordinate
does not mutate the prepared buffers. Shape-cost integration reads only visited
segments and materializes their projected point tuples for the existing native
integration API; these tuples are temporary, not a second retained network.
Small endpoint overlays still retain ordinary temporary tuples/lists.

No graph schema, dataset identity, path eligibility, hill model, projected units,
coordinate values, route ordering, tile version, or interval sampling changes.
Node metadata still retains WGS84 longitude/latitude scalars, and measurement
intervals retain their independent packed sampled vertices.

## Memory measurement

Reference: `50eb5bf`. The capital-area graph contains 508,727 nodes, 631,979
physical segments, and 1,263,958 directed arcs. Its graph file is 812,352,373 bytes;
dataset identity is
`fda8c40d3e79bbd078a7edcdc485fd7d45a5bd685fe05c0db32e9fd15567039f`.
Both measurements used standalone graph loads, without starting the backend.

| Retained graph data | Reference bytes | Packed bytes |
| --- | ---: | ---: |
| Graph document, including WGS84 geometry | 1,947,627,237 | 1,694,714,737 |
| Additional projected geometry | 372,493,776 | 49,972,752 |
| Additional integer graph | 377,113,732 | 367,985,384 |
| Other routing indexes | 211,698,518 | 211,698,518 |
| **Total graph + base routing** | **2,908,933,263** | **2,324,371,391** |

The total fell **584,561,872 bytes**, about **585 MB / 20.1%**. The remaining graph
and base indexes retain approximately **2.32 GB (2.16 GiB)**. Accounting recursively
uses `sys.getsizeof` with one visited-object set, counting shared objects once,
including memoryview owners and owned array payloads. It excludes measurement/
hill arrays, display data, extra policies, search/tile caches, interpreter overhead,
and allocator overhead. It is retained subsystem size, not whole-process RSS.

The projected container now references shared ID tuples before the integer graph
is traversed by the accounting code. Some ID-tuple allocation therefore moves
between the projected/integer rows; only the total is independent of traversal
order. The memory change is principally coordinate packing and map removal,
not a change to the arc representation.

## Equivalence and validation

The smaller `leppavaara-flat-10k` dataset has 69,707 physical segments and 278,839
measurement intervals, identity
`b74d86e5dcebdb20802ec502ed024214d8950f46fa2b0d7c9c589188d028d81d`.
The comparison against the preceding committed implementation checks:

- Every WGS84 segment vertex and projected segment/node coordinate, hashing
  the float64 values in shared integer order.
- Every measurement record and all four hill arrays.
- 400 full, reversed, and partial measurement inspections across 100 segments.
- Two viewport responses and 48 byte-identical encoded exploration tiles across
  two locations, zooms 13/15, 3/10 km budgets, all/surfaced/stair policies, and
  gradient/distance variants.
- 18 planner cases: three turnaround locations, three shape-cost
  widths, and hill preference off/high. Outcomes, selected arcs, costs, expansion
  counts, cost breakdowns, observations, traversals, and route coordinates match.

All compared outputs match. Full observations and hashes are in
[the benchmark data](39-packed-geometry.json). JSON comparison hashes normalize
integer versus float spelling, while the new synthetic artifact/checksum test
separately verifies exact spelling preservation. Tile hashes compare encoded bytes.

Standalone timing samples used separate processes rather than interleaved runs.
Planner timings include noisy outliers; tile timing samples are five direct
uncached encodes after warmup. These observations are smoke checks, not a speedup
claim. Startup still parses JSON and now packs coordinates, so no startup-time
improvement is claimed. Smaller-dataset load samples were 6.46 seconds for the
reference, 7.00 seconds in an earlier packed run, and 9.78 seconds in the final
run. The runs were not controlled timing experiments; packing adds startup work
and persistence is still needed to avoid it on each restart.
Tests and comparisons used Python 3.14.6 on macOS 26.6.2
arm64. All **226 tests passed**, including new buffer ownership, read-only,
checksum/export, detached coordinate, slice/reversal, empty graph, policy sharing,
overlay sharing, and index-order guards.

The user-stopped backend remains stopped. No live-server or browser acceptance
check is claimed. Prepared-array persistence remains the startup-focused next step;
large raw node/segment/arc dictionaries and policy adjacency remain separate work.

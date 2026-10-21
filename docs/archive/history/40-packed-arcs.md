# Packed arcs and shared adjacency — 2026-10-21

This increment replaces retained arc dictionaries, per-arc Python records, and
string adjacency lists with packed columns and shared numeric adjacency. Policy
views retain eligibility masks over the same topology. Remaining metadata,
display packing, and persistent startup artifacts stay in the [roadmap](../../roadmap.md).

## Representation

Arc source, target, and physical-segment indices are read-only int64 columns;
lengths and optional ascent/descent/terrain metadata are float64. Separate
direction and presence/integer-spelling flags preserve record semantics and
canonical JSON identity. Existing external ID tuples and lookup maps remain.
Outgoing/incoming adjacency consists of int64 arc-index buffers with int64 node
offsets. Stable preparation preserves arc order within each node's row, including
parallel arcs and loops. Prepared integer arc records are no longer retained.
Indexed callbacks receive small transient immutable `IntegerArc` named tuples.

`ArcRecords`, `ArcLookup`, and `AdjacencyLookup` provide on-demand external views.
They reconstruct only requested records/rows and retain no dictionary/list cache.
Copies of published graph documents materialize editable arc records; normal policy
construction and request overlays do not copy the document. Full artifact export
materializes views at its JSON boundary. Canonical graph hashing batches arcs in
groups of 1,024, avoiding reconstruction of the entire arc dictionary tree while
preserving the existing checksum algorithm's exact output.

Each policy owns a sealed, one-byte-per-arc eligibility mask plus its existing
blocked-node/classification/direction metadata. Arc columns, ID maps, and both
adjacency buffers are shared. Relaxing a policy recomputes the mask over the full
topology, restoring eligible connections. Endpoint overlays append only a few
records and replace affected adjacency rows with integer arc indices. Their sparse
external records preserve legacy callbacks and do not alter base arrays.

Bounded Dijkstra reads numeric columns directly, avoiding per-arc record creation.
A* materializes only callback records as needed. Default ascent/overlap costs use
numeric metadata and segment indices. Legacy callbacks and selected-route response
assembly remain supported through the external views. The building-access peel
uses prepared segment directions instead of scanning/materializing every arc.

## Capital-area retained memory

Reference: `3645314`. The standalone capital-area graph has 508,727 nodes, 631,979
physical segments, and 1,263,958 directed arcs, dataset identity
`fda8c40d3e79bbd078a7edcdc485fd7d45a5bd685fe05c0db32e9fd15567039f`.

| Retained graph data | Reference bytes | Packed bytes |
| --- | ---: | ---: |
| Graph document and referenced prepared data | 1,694,714,737 | 1,115,816,002 |
| Additional projected geometry | 49,972,752 | 40,847,008 |
| Additional integer graph | 367,985,384 | 166,594,827 |
| Other base routing indexes | 211,698,518 | 66,753,838 |
| **Total graph + base routing** | **2,324,371,391** | **1,390,011,675** |

The total falls **934,359,716 bytes**, about **934 MB / 40.2%**. Graph and base routing
storage is now approximately **1.39 GB (1.29 GiB)**. Recursive `sys.getsizeof`
accounting uses one visited-object set and includes memoryview owners/array payloads.
It excludes measurements, hills, display data, extra policies, caches, and
interpreter/allocator overhead. This is retained subsystem size, not process RSS.
Shared ID tuples/array owners move between attribution rows as traversal reaches
them; only the total is independent of attribution order. The backend was left
stopped throughout these standalone checks.

## Eligibility mask versus filtered adjacency

An alternating seven-repeat comparison on `leppavaara-flat-10k` covers all/surfaced
policies, 1/10/30 km budgets, and forward/reverse search. Both layouts produce
identical distances and discovery ordering. Shared-mask and filtered-row searches
are within roughly 3% in the final samples. Filtered paved-policy rows are usually
slightly faster, but require a separate adjacency allocation per policy.

For this smaller graph, shared incoming/outgoing buffers total 4,010,704 payload
bytes. A policy mask uses 139,414 bytes; separate filtered buffers require 3,885,840
bytes for all-paths/no-stairs or 2,699,520 bytes for surfaced/no-stairs. The retained
choice is shared topology plus masks. The capital-area mask is 1,263,958 bytes per
policy. Revisit filtered rows if measured interaction latency justifies their
additional storage; no user-facing strategy switch is introduced.

## Validation and latency tradeoff

All **230 tests pass**. New tests cover canonical checksums across the batching
boundary, optional fields and numeric spelling, detached records, read-only
columns and masks, policy topology sharing/restoration, filtered-row equivalence,
and sparse overrides of existing base arcs. Existing generated graph comparisons
cover blocking, direction, reverse search, budgets, deadlines, and equal-cost ties.
The experimental integer-index preparation benchmark was adapted and smoke-tested.

Real-data comparison against the preceding implementation preserves every
measurement record, all four hill arrays, all WGS84/projected coordinates,
400 full/reverse/partial inspections, two viewports, 48 byte-identical encoded
tiles, and 18 planner cases (paths, costs, expansion counts, breakdowns, traversals,
observations, and coordinates). Twelve bounded-search cases also retain exact
distance values and discovery ordering. Dataset identity is unchanged. Raw
observations and hashes are in [the benchmark data](40-packed-arcs.json).

This is a memory improvement with a search-time cost. In seven-repeat standalone
samples on the smaller dataset, all-paths large-budget Dijkstra increases from
roughly 33–37 ms to 38–39 ms; surfaced searches increase from roughly 18 ms to
22–23 ms. One-kilometre searches increase from approximately 1.13 to 1.50 ms for
all paths, and 0.70 to 0.95 ms for surfaced paths. Scalar reads and temporary values
replace preallocated Python arc objects. Many planner smoke cases are also slower;
individual samples have outliers, so no speedup is claimed. These are separate
process observations, not controlled application interaction measurements.

The single dataset-load samples were 7.40 seconds before and 6.52 seconds after.
They do not establish a startup improvement. JSON parsing, geometry/measurement
packing, and topology construction remain on startup. Persisted prepared arrays
remain the next startup-focused step. The server was not started and browser
acceptance remains open. Measurements used Python 3.14.6 on macOS 26.6.2 arm64.

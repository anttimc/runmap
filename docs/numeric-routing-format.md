# Numeric routing artifact, version 2

This implements numeric storage for the
[100 ms routing architecture](100ms-routing-architecture.md). It is an offline
artifact alongside the reference database, used by the HTTP planner and distance
exploration with bounded working-set caching.

The [sqleton schema visualization](reference/numeric-routing-schema.svg) shows
the database tables, indexes, and declared foreign keys. See
[generation instructions](reference/schema-visualization.md) to refresh it.

## Export and read

```sh
uv run python -m runmap.cli paths export-numeric \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  data/paths/prepared/helsinki-capital-area/numeric-routing-v2.sqlite
```

The source must be a current prepared SQLite network. Export processes its spatial
chunks individually, including prepared features; it never loads the complete
JSON graph. The destination must be new. Staging and atomic no-overwrite publication
preserve both existing artifacts and failed-export cleanup.

New exports retain a segment only when at least one supported metadata profile
allows at least one traversal direction. Its arcs, geometry, and hill features
are omitted otherwise; nodes with no retained incident segments and empty chunks
are omitted too. Chunk envelopes and global node incidence are recomputed over
this policy union. Excluded links therefore do not create unloaded frontiers,
while retained links outside a working window still do. Source network data,
coverage, snapping, and inspection remain available independently.

The JSON metadata value `numeric_preprocessing_version` is `"policy-union-v1"`
for these exports. Existing version-2 artifacts remain readable; re-export to a
new destination to obtain the reduction. Source numeric identities are preserved,
including identity rows for omitted records. Those rows do not imply graph
membership: native endpoints must belong to the loaded retained graph.
The [capital-area pruning report](archive/history/49-policy-union-pruning.md)
records size changes, routing/exploration equivalence, and latency limits.

New exports use SQLite schema version 2, separating detailed hill intervals from
the main graph payload. Readers accept schema 1 artifacts with embedded intervals
and schema 2 artifacts with separate detail. The binary chunk layout remains
version 2. Rebuild the native extension before using the new exporter/runtime;
older runtimes reject the new SQLite schema. Re-export to a new destination and
restart the API to obtain the smaller working sets.

Version 2 adds explicit node eligibility masks. Arc eligibility alone cannot
represent the reference behavior for a search starting at a blocked node.
Version-1 experimental artifacts must be re-exported into a new filename. This
does not change the reference network database schema.

The artifact is read-only SQLite with a bounded page-cache target. Opening reads
metadata only. `iter_numeric_chunks` selects full chunk envelopes through an
R-tree and wraps the BLOB payloads in NumPy array views. It creates a fixed number
of array descriptors per chunk, without per-node, per-edge, or per-interval Python
records. It does not build a dense regional graph. Consumers must filter
`segment_bounds` for exact window membership; candidate chunks may include other
segments. This conservative candidate behavior retains long crossing segments.

`iter_numeric_payloads` supplies the same immutable bytes directly to Rust,
avoiding the intermediate NumPy descriptors when a native graph is loaded.

SQLite materializes a bytes payload per loaded chunk. Array views retain that
immutable bytes owner and remain valid after the cursor or connection closes.
This is direct buffer decoding, not memory mapping or a zero-copy path from disk.
A future byte-bounded cache can retain these immutable chunk owners; eviction must
not invalidate views retained by an active search or exploration snapshot.

## Identity and chunk connections

Metadata copies the source dataset/feature identities, model versions, profiles,
coverage, distance scale, and terrain availability. Numeric artifact schema and
chunk format versions are independent of the reference database schema.

Node IDs are source SQLite node row IDs captured during this export. Segment and
arc IDs are their persisted source numbers. These IDs are stable within the
artifact generation; they are not stable identifiers across unrelated exports or
rebuilds. Resolve descriptive IDs lazily through `identities(kind, number,
external_id)`. Reuse is keyed by dataset and feature identity, not a filename.

Each arc and physical segment belongs to one chunk. Endpoint nodes can occur in
multiple chunks with the same numeric node ID and saved global incidence count.
`node_chunks(node_id, chunk_id)` indexes all chunks incident to a node. This is the
boundary-link contract for native traversal: a loaded chunk's local node
index is not a global node index. Searching across that node must include its
other incident chunks, subject to request bounds and limits. Missing loaded links
must remain distinguishable from source coverage boundaries and true dead ends.

## Binary layout

Signal passages are loaded separately from an optional identity-validated
[`signals.sqlite` sidecar](reference/prepared-data.md#traffic-light-sidecar).
The main graph binary layout stays at version 2. Python passes bounded gate and
movement rows into native maps; their estimated retained bytes count toward the
working-set cache budget. No raw OSM evidence enters a request working set.
Signals On uses movement labels and carries arrival context across run legs;
Signals Off keeps the existing node-label search and compact `EdgeRef` predecessors.
The private binding serializes five base cost components followed by two passage
counts per traversal; the Python adapter exposes separate component/count arrays.


`numeric_chunks` stores a payload, byte count, and SHA-256 per chunk. An unsigned
little-endian header, described by `struct.Struct("<8sI4x6Q")`, contains:

- Eight-byte magic `RUNNUM02` and format version `2`.
- Node, segment, arc, profile, projected point, and hill interval counts.

Header size is 64 bytes. Each following column starts at an offset divisible by
64 relative to the payload. Arrays use little-endian int64/float64 or uint8;
there is no pickle, object dtype, compression, or embedded descriptive JSON.
The ordered layout is defined by `array_layouts` in
`src/runmap/paths/numeric_format.py`:

| Columns | Meaning |
| --- | --- |
| `node_ids`, `node_xy`, `node_degree` | Global numeric identities, projected coordinates, global incidence |
| `node_allowed` | Node eligibility in metadata profile order, including access restrictions |
| `segment_ids`, `segment_nodes`, `segment_bounds` | Global segment identities, local node indices, projected envelopes |
| `geometry_offsets`, `geometry_xy` | Segment polylines as offsets into projected point columns |
| `arc_ids`, `arc_nodes`, `arc_segments`, `arc_lengths`, `arc_reverse` | Global arc identities, local endpoint/segment indices, physical lengths, direction |
| `arc_allowed` | Arc eligibility by metadata profile order, including prepared access/boundary rules |
| `outgoing_offsets`, `outgoing_arcs` | Local-node CSR adjacency referencing local arc indices |
| `incoming_offsets`, `incoming_arcs` | Corresponding reverse-search adjacency |
| `hill_offsets`, `hill_chainage`, `hill_features`, `hill_totals` | Legacy embedded intervals and complete-segment directional totals; schema 2 uses zero offsets and empty interval columns |

Projected coordinates use EPSG:3067. Forward/backward hill features have shape
`(intervals, 2, 7)` and totals have shape `(segments, 2, 7)`. Feature order is
penalty, ascent, descent, valid length, trusted length, fallback length, total
length. Direction index 0 is forward and 1 is backward. Geometry and hill offset
columns have segment count plus one elements. Arc direction flag 0 is forward;
1 is backward. All geometry remains in physical segment order.

Without prepared hills, interval columns are empty and totals are zero; the
metadata hill model is absent. Consumers must use that availability metadata so
an unavailable observation is not interpreted as measured zero.

With prepared hills in SQLite schema 2, interval columns are also empty, but
directional totals remain populated. Complete-segment traversal reads those
totals. Hills-enabled planning loads detail only for snapped endpoint segments;
distance exploration and hills Off load no interval detail. Endpoint detail is
request-local, so graph-cache keys and owners stay independent of endpoints and
hill preferences. Legacy embedded intervals remain usable without conversion.

### Separate endpoint intervals

Schema 2 stores `hill_intervals(segment_id, payload, sha256, byte_size, raw_size)`.
The key is the original numeric physical-segment ID. Payloads use lossless zlib
compression (export level 1); the checksum and byte size describe the compressed
BLOB. `raw_size` bounds decompression, which must consume one complete stream
and produce exactly that many bytes. Missing records and corrupt data fail the
request rather than silently making partial traversal free.

Decompressed bytes contain little-endian float64 rows of 16 values (128 bytes):
graph chainage start/end, seven forward features, and seven backward features in
the order above. Raw interval values are preserved exactly. Rust checks finite,
nonnegative values, contiguous increasing spans, and coverage of the original
segment before integrating a partial span. Physical sampled length in the seven
features can differ from graph chainage length; complete traversal is identified
using the original arc length, not sampled length. The Python endpoint container
owns immutable detail bytes; native search copies only these small endpoint
arrays into request-local state before releasing the GIL.

Export validates finite values, dimensions, references, incidence, CSR consistency,
bounds, flags, and IDs, then stores a checksum. Decoding always validates header,
version, and exact payload length before constructing views. Optional checksum
verification detects payload changes. Numerical semantic validation is available
through `validate_numeric_chunk`; normal loading avoids repeating all offline
validation scans. Bounds are conservative source R-tree envelopes.

## Benchmarks

The [separate-interval report](archive/history/51-split-hill-intervals.md) records
current paired storage, complete-request, and exploration-preparation comparisons.

Record the reference request including snapping, JSON serialization, and coarse
phase timings in a fresh worker:

```sh
uv run python -m runmap.experiments.benchmark_run_latency \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --home 24.795 60.216 --turnaround 24.815 60.225 \
  --hills high --avoidance-ratio .18 --repeats 5 \
  --report artifacts/pathfinding-profile/reference-latency.json
```

The report separates metadata startup, the first request, and repeated requests;
contains p50/p95/p99 summaries and response hashes; and labels nested phases.
It does not clear OS caches or simulate HTTP transport. Few samples are useful
for local comparisons, not production tail-latency claims.

Measure numeric chunk retrieval and view creation independently:

```sh
uv run python -m runmap.experiments.benchmark_numeric_chunks \
  data/paths/prepared/helsinki-capital-area/numeric-routing-v2.sqlite \
  --bounds 374971.5666 6675095.1128 381827.5009 6680883.1673 \
  --repeats 10 --trace-allocations \
  --report artifacts/pathfinding-profile/numeric-buffer-latency.json
```

This reports loaded chunks/bytes, exact selected segment count, first/repeated
buffer-access times, and an optional separate allocation measurement. Chunk
references are released between samples. No application chunk cache is involved.
Buffer-loading times must not be presented as complete planning latency.

An initial local measurement on the version-1 Helsinki artifact selected 44,366 segments
from 221 candidate chunks, reading 47,011,360 payload bytes including hills.
The first buffer load took approximately 140 ms; ten repeated loads had a median
of approximately 50 ms. A separate traced allocation load peaked at approximately
46 MiB. The artifact occupied approximately 738 MiB. OS cache state was
uncontrolled, and no application cache was used. These results establish a
storage baseline; native search, incremental loading, and residency caching are
still required to evaluate the complete 100 ms request target.

## Native traversal milestone

Rebuild the extension after changing the Rust sources using the development
instructions (`uv run --no-sync maturin develop --release --locked --uv` with the
Rust toolchain in PATH).

`load_numeric_graph` in `runmap.routing.numeric_search` loads candidate bytes and
builds a Rust node lookup. The native graph owns references to immutable Python
bytes; native search borrows checked column slices and releases the GIL. There
is no payload copy into per-edge Rust or Python graph records. Rust validates
shape/offset/reference consistency and shares cross-chunk nodes by numeric ID.
Native lookup and search state are numeric Rust allocations, not Python records.

`numeric_distances` supports forward/reverse traversal, distance budgets, partial
segment starts, profile masks, and unloaded-frontier output. `numeric_shortest_path`
adds A*, partial start/destination overlays, complete/partial hill integration,
shape-field integration, and physical-span reuse costs. `numeric_plan_run`
constructs a single outward route for one-way mode or both two-leg side assignments
for round-trip mode under one deadline. Result arrays
are immutable byte-backed NumPy views. Overlay nodes use negative request-local
IDs; reuse a segment endpoint rather than passing an overlay node ID to another
request.

The native graph is immutable and can serve concurrent searches. Each search owns
its distances, predecessors, endpoint overlay, and field cache. There are no
Python callbacks in the visited-edge loop. Equal-cost ties are deterministic by
numeric node identity and can select different routes than the reference's string
identity ties. The experimental planner does not use the reference's special
zero-width reverse-path shortcut. Native-versus-reference validation therefore
compares legality, costs, spans, and observations rather than requiring every
equal-cost arc sequence to match.

Run planning and distance-exploration HTTP endpoints use this layer when a matching artifact is
available. It preserves validation, working-area limits, expansion, and response
rendering, and caches immutable graphs in a byte-accounted LRU per artifact
lifetime. API endpoint overlays pass snapped projected coordinates as well as
chainage so shape integration uses the reference endpoint geometry. The graph
loads selected topology, geometry, and hill totals; schema 2 keeps interval detail
outside these BLOBs. Different
bounds do not share retained chunks yet. Exploration snapshots retain only sparse
reached-node IDs/distances; numeric endpoint metadata for inspected segments is
queried on demand from the source database. Their byte/TTL budget excludes graph
owners because snapshot result bytes remain valid independently of graph-cache
eviction or shutdown. Profiles, partial starts, whole-interval inclusion, and
frontier expansion retain reference semantics. See [API setup](development.md#export-a-sqlite-network-dataset)
and [complete request timings](100ms-routing-architecture.md).

```sh
uv run python -m runmap.experiments.benchmark_native_routing \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  data/paths/prepared/helsinki-capital-area/numeric-routing-v2.sqlite \
  --hills high --repeats 10 --compare-reference \
  --report artifacts/pathfinding-profile/native-routing-high.json
```

For the representative Leppävaara case on 44,366 selected segments, ten local
native run samples had medians of approximately 5 ms with hills off and 9 ms
with hills high. Both include two side assignments, their outbound/return
searches, shape costs, and reuse costs. The 1 km distance exploration reached
2,251 nodes in approximately 0.24 ms median. Run costs differed from the reference
by less than `1e-9` metres in these two observations.

Payload loading varied from approximately 67 to 165 ms with uncontrolled OS cache
state; native validation/indexing took approximately 10–12 ms, and snapping took
approximately 32–38 ms. The first fixed working set, including snapping,
preparation, and initial native run, took approximately 122–224 ms. These are
primitive measurements, exclude HTTP rendering/serialization and automatic
window expansion, and do not establish a complete 100 ms request guarantee.

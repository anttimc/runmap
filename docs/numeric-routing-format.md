# Numeric routing artifact, version 1

This is the first storage implementation for the
[100 ms routing architecture](100ms-routing-architecture.md). It is an offline
artifact alongside the reference database. The HTTP planner and exploration
endpoints still use the reference backend; native traversal and bounded chunk
residency have not been connected yet.

## Export and read

```sh
uv run python -m runmap.cli paths export-numeric \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  data/paths/prepared/helsinki-capital-area/numeric-routing-v1.sqlite
```

The source must be a current prepared SQLite network. Export processes its spatial
chunks individually, including prepared features; it never loads the complete
JSON graph. The destination must be new. Staging and atomic no-overwrite publication
preserve both existing artifacts and failed-export cleanup.

The artifact is read-only SQLite with a bounded page-cache target. Opening reads
metadata only. `iter_numeric_chunks` selects full chunk envelopes through an
R-tree and wraps the BLOB payloads in NumPy array views. It creates a fixed number
of array descriptors per chunk, without per-node, per-edge, or per-interval Python
records. It does not build a dense regional graph. Consumers must filter
`segment_bounds` for exact window membership; candidate chunks may include other
segments. This conservative candidate behavior retains long crossing segments.

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
boundary-link contract for future native traversal: a loaded chunk's local node
index is not a global node index. Searching across that node must include its
other incident chunks, subject to request bounds and limits. Missing loaded links
must remain distinguishable from source coverage boundaries and true dead ends.

## Binary layout

`numeric_chunks` stores a payload, byte count, and SHA-256 per chunk. An unsigned
little-endian header, described by `struct.Struct("<8sI4x6Q")`, contains:

- Eight-byte magic `RUNNUM01` and format version `1`.
- Node, segment, arc, profile, projected point, and hill interval counts.

Header size is 64 bytes. Each following column starts at an offset divisible by
64 relative to the payload. Arrays use little-endian int64/float64 or uint8;
there is no pickle, object dtype, compression, or embedded descriptive JSON.
The ordered layout is defined by `array_layouts` in
`src/runmap/paths/numeric_format.py`:

| Columns | Meaning |
| --- | --- |
| `node_ids`, `node_xy`, `node_degree` | Global numeric identities, projected coordinates, global incidence |
| `segment_ids`, `segment_nodes`, `segment_bounds` | Global segment identities, local node indices, projected envelopes |
| `geometry_offsets`, `geometry_xy` | Segment polylines as offsets into projected point columns |
| `arc_ids`, `arc_nodes`, `arc_segments`, `arc_lengths`, `arc_reverse` | Global arc identities, local endpoint/segment indices, physical lengths, direction |
| `arc_allowed` | Arc eligibility by metadata profile order, including prepared access/boundary rules |
| `outgoing_offsets`, `outgoing_arcs` | Local-node CSR adjacency referencing local arc indices |
| `incoming_offsets`, `incoming_arcs` | Corresponding reverse-search adjacency |
| `hill_offsets`, `hill_chainage`, `hill_features`, `hill_totals` | Packed hill intervals and complete-segment directional totals |

Projected coordinates use EPSG:3067. Forward/backward hill features have shape
`(intervals, 2, 7)` and totals have shape `(segments, 2, 7)`. Feature order is
penalty, ascent, descent, valid length, trusted length, fallback length, total
length. Direction index 0 is forward and 1 is backward. Geometry and hill offset
columns have segment count plus one elements. Arc direction flag 0 is forward;
1 is backward. All geometry remains in physical segment order.

Without prepared hills, interval columns are empty and totals are zero; the
metadata hill model is absent. Consumers must use that availability metadata so
an unavailable observation is not interpreted as measured zero.

Export validates finite values, dimensions, references, incidence, CSR consistency,
bounds, flags, and IDs, then stores a checksum. Decoding always validates header,
version, and exact payload length before constructing views. Optional checksum
verification detects payload changes. Numerical semantic validation is available
through `validate_numeric_chunk`; normal loading avoids repeating all offline
validation scans. Bounds are conservative source R-tree envelopes.

## Benchmarks

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
  data/paths/prepared/helsinki-capital-area/numeric-routing-v1.sqlite \
  --bounds 374971.5666 6675095.1128 381827.5009 6680883.1673 \
  --repeats 10 --trace-allocations \
  --report artifacts/pathfinding-profile/numeric-buffer-latency.json
```

This reports loaded chunks/bytes, exact selected segment count, first/repeated
buffer-access times, and an optional separate allocation measurement. Chunk
references are released between samples. No application chunk cache is involved.
Buffer-loading times must not be presented as complete planning latency.

An initial local measurement on the Helsinki artifact selected 44,366 segments
from 221 candidate chunks, reading 47,011,360 payload bytes including hills.
The first buffer load took approximately 140 ms; ten repeated loads had a median
of approximately 50 ms. A separate traced allocation load peaked at approximately
46 MiB. The artifact occupied approximately 738 MiB. OS cache state was
uncontrolled, and no application cache was used. These results establish a
storage baseline; native search, incremental loading, and residency caching are
still required to evaluate the complete 100 ms request target.

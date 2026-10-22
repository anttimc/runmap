# Selective SQLite API delivery

Recorded 2026-10-22. Maintained behavior and setup are in
[architecture](../../architecture.md#sqlite-storage-foundation) and
[development](../../development.md#export-a-sqlite-network-dataset).
Remaining local-search work is in the [roadmap](../../roadmap.md#next-sqlite-storage-and-bounded-network-loading).

## Delivered behavior

The backend prefers an explicitly configured SQLite dataset, or `network.sqlite`
beside the configured JSON graph. Config, static MVTs, viewport responses,
segment inspection, and snapping use selective reads. Startup reads metadata
only, including precomputed presentation/hidden-count information. Two read-only
connections are leased exclusively to requests and closed on shutdown; cached
encoded tiles retain their byte budget. No decoded network is retained by these
delivery paths.

Exploration, snapshots, inspection with distance-from-start, and run planning
temporarily load the matching JSON graph on first use. That bridge validates
graph and measurement identities, shares one loaded dataset, and remembers load
failures. It still has full-dataset memory and first-search loading costs; this
change does not implement bounded graph search or planner area expansion.

Ten new tests cover configuration/inspection/viewport/snap parity across policies;
decoded static-tile geometry/properties across four policies, four variants, and
four zooms; hidden building access; metadata-only startup SQL; no source reads;
lazy planning/snapshot compatibility; stale routing identity; bounded concurrent
connection leases; shutdown; missing presentation metadata; stale API identities;
missing routing data; and ETag/304 responses. The full Python suite passed
263 tests.

## Real-extract comparison

```sh
uv run --extra web python -m runmap.experiments.benchmark_database \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  --bounds 374750 6674750 375250 6675250 --api \
  --report docs/archive/history/45-sqlite-api.json
```

The [raw report](45-sqlite-api.json) records graph/model identities and machine
versions. The extract contains 69,707 segments and 278,839 measurement intervals.
Offline export runs in the parent process; each delivery backend runs in a
separate fresh reader process. Peak reader RSS therefore excludes export.

| Measurement | SQLite | JSON reference |
| --- | --- | --- |
| Dataset initialization, excluding Python imports | 9.67 ms | 8,406.22 ms |
| Peak reader RSS, including imports and delivery | about 82 MiB | about 1,014 MiB |
| First / repeated viewport query | 12.90 / 5.02 ms | 418.37 / 5.37 ms |
| First / repeated snap | 0.66 / 0.66 ms | 2.75 / 2.67 ms |
| First / repeated segment inspection | 0.84 / 0.81 ms | 0.79 / 0.76 ms |
| First uncached tile | 28.12 ms | 35.80 ms |
| Repeated cached tile | 0.014 ms | 0.014 ms |

Both readers returned 717 viewport intervals and the same 47,484-byte tile.
Canonical viewport SHA-256 and encoded tile SHA-256 match across readers; the
probe fails if either differs. The SQLite reader confirms `legacy_loaded=false`.
The first JSON viewport query prepares its policy index; SQLite uses the
persisted policy results.

The OS cache is warm from export. These are single first/repeated observations,
not cold-disk or p50/p95 measurements. Direct delivery functions exclude HTTP,
transport, browser rendering, and viewport JSON serialization time. Reader RSS
is a process peak, not retained heap size. The 500 × 500 m projected box becomes
a conservative WGS84 envelope for viewport comparison. Broader geographic coverage,
concurrent HTTP traffic, and search memory/latency remain acceptance work.

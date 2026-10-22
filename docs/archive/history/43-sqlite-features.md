# Prepared SQLite features

Recorded 2026-10-22. This is supporting evidence; maintained behavior is in
[architecture](../../architecture.md#sqlite-storage-foundation) and remaining
API/local-search work is in the [roadmap](../../roadmap.md#next-sqlite-storage-and-bounded-network-loading).

## Change and validation

Schema version 2 adds indexed individual measurements/display intervals, flat-run
thresholds, source-boundary flags, full-network building-access classifications,
prepared boundary restrictions, all four surface/stair policies, projected
node/segment coordinates, and directional hill totals/intervals. A feature identity
binds source identities to model versions/settings. Version 1 or changed model
definitions require a new export.

Six new parity tests compare selectively read data with the full-network loader.
They cover exact samples/nulls/flags and thresholds; individual interval selection;
projected coordinates; policy directions/reasons; full and partial hill traversals
in both directions; node access and boundary exclusions; empty-sample fallback;
and stale schema/model/identity rejection. A small window retaining only a driveway
tip preserves its full-network branch attachment/classification. Read tests forbid
reading source JSON or invoking preparation.

All 247 tests passed in an isolated copy of the last committed code plus the SQLite
changes. The combined working tree subsequently passed 251 tests alongside the
concurrent metadata-packing work. Existing SQLite publication/read-only/checksum
tests also pass. No API endpoint has switched storage in this change.

## Real-extract storage probe

Reproduce from the repository root:

```sh
uv run python -m runmap.experiments.benchmark_database \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  --bounds 374750 6674750 375250 6675250 \
  --report docs/archive/history/43-sqlite-features.json
```

The [machine-readable report](43-sqlite-features.json) identifies the graph,
prepared features, machine architecture, Python, and SQLite. On Darwin ARM64:

| Measurement | Observed |
| --- | --- |
| Dataset | 55,627 nodes, 69,707 segments, 278,839 measurement/display intervals |
| Offline export | 26.26 s |
| Database size | 474,775,552 bytes (452.8 MiB) |
| Process peak RSS, including offline import | 939,442,176 bytes (895.9 MiB) |
| Read-only open and metadata validation | 13.95 ms |
| Selected 500 × 500 m area | 649 visible intervals, 124 complete segment candidates |
| First / repeated display query | 7.77 / 2.62 ms |
| First / repeated prepared-segment query | 28.08 / 5.18 ms |

The database is deleted after the probe. The reader uses a fresh connection after
export, so the OS cache is warm. These are individual observations, not p50/p95
or cold-disk measurements. Peak memory includes full offline preparation and does
not measure a separate reader process. The feature-rich database adds prepared
data and indexes; reduced disk size is not established. API tile encoding,
request concurrency, larger coverage, and bounded graph searches remain unmeasured.

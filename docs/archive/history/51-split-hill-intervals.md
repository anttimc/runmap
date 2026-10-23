# Separate hill interval detail

Measured on 2026-10-23 with macOS 26.6.2 ARM64, Python 3.14.6, and an optimized
native build. [The report](51-split-hill-intervals.json) preserves dataset/feature
identities, endpoints/settings, storage sizes, and timing summaries.

## Storage and runtime

New numeric exports use SQLite schema 2. Shared binary version-2 chunks retain
directional full-segment totals and contain no detailed intervals. Separate
per-segment records retain the original interval values, compressed losslessly
with zlib level 1. Endpoint records have compressed and raw byte counts and a
SHA-256; decompression is bounded and native parsing validates chainage/features.
See [the maintained format](../../numeric-routing-format.md#separate-endpoint-intervals).

Hills-enabled planning loads detail for its two snapped endpoint segments under
the existing artifact connection lease. Detail bytes belong to endpoint
containers and native request state, not the cached graph. Full-segment search
and route observations use the existing directional totals. Partial spans are
integrated in Rust without Python callbacks. Graph cache keys remain bounds-only,
including when endpoints move or hills are toggled. Hills Off and distance
exploration load no interval records.

The reader still accepts SQLite schema 1 with embedded intervals. New SQLite
schema 2 artifacts require the updated native extension and are rejected by old
runtimes. Source metadata, numeric IDs, policy masks, geometry, model identities,
and the reference network database are preserved.

## Size

Both artifacts use policy-union pruning and contain the same 586,218 physical
segments and 2,616,596 hill intervals.

| Metric | Embedded intervals | Separate intervals |
| --- | ---: | ---: |
| Main graph payload bytes | 582,072,800 | 246,980,448 |
| Full SQLite artifact bytes | 740,032,512 | 619,126,784 |
| Separate compressed interval bytes | — | 123,759,656 |
| Separate raw interval bytes | — | 334,924,288 |

Graph payload shrank 57.6%; the complete artifact shrank 16.3%. A preliminary
uncompressed per-segment layout grew the file because of SQLite record/page
overhead; compression prevents that regression. These are stored/loaded byte
measurements, not peak or retained process RSS measurements.

## Complete requests

One first request and three repeated requests were measured per case and
artifact. Every request reran planning; graph caching remained enabled. Variant
order alternated by hill setting. OS cache was uncontrolled. Timings include
handler execution and JSON serialization, excluding HTTP/browser work. These
small samples are diagnostics, not production percentiles or cold-disk claims.

| Request | Before repeated median | After repeated median |
| --- | ---: | ---: |
| Short, hills Off | 22.56 ms | 21.72 ms |
| Short, hills High | 31.98 ms | 30.48 ms |
| Long, hills Off | 1,852 ms | 1,010 ms |
| Long, hills High | 1,733 ms | 1,157 ms |
| Long preparation, hills Off | 1,306 ms | 563 ms |
| Long preparation, hills High | 1,175 ms | 605 ms |

All response fields matched, excluding elapsed time. The comparison allowed
`1e-8` absolute / `1e-12` relative floating tolerance, but the observed maximum
floating difference was zero in all four cases. Geometry, traversals, costs,
observations, expansion counts, and area metadata therefore matched exactly.
Earlier count-query timings used a different sample/cache state; compare the
paired measurements above rather than combining timings from separate reports.

## Distance exploration

The same start, all surfaces, stairs avoided, and 1/2 km budgets produced exactly
matching reached global node IDs and distances. No interval-detail reads were
permitted during either preparation. Each comparison included a first graph
load and three repeated preparations, without a snapshot-result cache.

| Forward budget | Before graph bytes | After graph bytes | First preparation before/after |
| --- | ---: | ---: | ---: |
| 1 km | 10,936,048 | 5,240,944 | 53.9 / 30.2 ms |
| 2 km | 25,912,192 | 11,913,920 | 110.0 / 41.0 ms |

Repeated preparation was already approximately 0.6/1.6 ms at 1/2 km and remained
approximately 0.6/1.7 ms. This does not measure tile delivery or browser work.

## Checks and local selection

All 344 Python tests and five Rust tests pass. Rust formatting and Clippy checks
pass, including Python bindings. Regressions cover partial/same-segment/reversed
traversals, complete totals without intervals, missing/malformed/incomplete
detail, checksums, legacy embedded intervals, concurrent cache reuse across hill
settings, and exploration without detail reads.

Raw samples and reproduction scripts are retained locally under
`artifacts/pathfinding-profile/split-hills/`:

```sh
uv run --no-sync python artifacts/pathfinding-profile/split-hills/benchmark.py
uv run --no-sync python artifacts/pathfinding-profile/split-hills/exploration.py
```

The verified artifact is selected at the default `numeric-routing-v2.sqlite`
path. Its predecessor is retained as `numeric-routing-v2-before-split-hills.sqlite`.
Restart the API to open the new artifact and native extension generation.

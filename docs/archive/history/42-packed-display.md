# Packed display metadata — 2026-10-22

Display metadata now shares measurement interval offsets rather than retaining
a Python segment/index record for every interval. Surface classes are stored
once per segment. Flat-run thresholds use immutable float64 values and a separate
presence byte, preserving zero versus missing values. `DisplayIntervals` is a
read-only sequence view; indexing reconstructs a detached `DisplayInterval`.
Slicing, reverse traversal, and negative indexing preserve sequence behavior.
Preparation validates interval count/order and per-segment surface consistency,
then releases the original interval list before hill construction.

This supports the updated SQLite plan: offsets and dense segment references
belong to the loaded measurement store, which can represent one network window.
No database schema, external identities, serialized artifacts, or runtime
database loading change here. Flat-run calculation still runs on full prepared
segments; it must be persisted during database preparation before selective API
delivery, as required by the roadmap. This increment does not make globally
loaded arrays a prerequisite for SQLite delivery.

## Measurements and validation

Dataset `leppavaara-flat-10k` has 69,707 segments and 278,839 intervals.
Python 3.14.6 on macOS 26.6.2 arm64:

| Retained display metadata | Bytes |
| --- | ---: |
| Previous interval objects | 22,028,959 |
| Packed metadata | 3,068,452 |

The reduction is **18,960,507 bytes / 86.1%**. Recursive `sys.getsizeof` includes
buffer owners and excludes the already-owned shared measurement offset array.
The reference independently rebuilds the previous layout from measurements,
including shared per-segment numbers and flat-threshold objects. This is retained
subsystem storage, not whole-process RSS or peak startup memory.

Every reconstructed interval matches. Eight complete viewport responses and
sixteen static gradient tiles match exactly across two locations, zooms 13/15,
building-access filtering on/off, and all/surfaced policies. The current suite
passes **241 tests**, including SQLite foundation tests developed concurrently.
New display tests cover buffer ownership/read-only access, detached records,
null/zero thresholds, slicing, invalid layouts, and empty segments before/after
populated ones.

Ten alternating uncached tile encodes per case show a latency cost. Zoom-15
medians rise by roughly 2–3 ms (about 6–7%). Zoom-13 results are mixed and noisy:
approximately 181–490 ms before and 192–510 ms after, with individual paired
changes between roughly -40 and +87 ms. Scalar reads, offset lookup, and temporary
interval records replace retained Python objects. No application speedup is
claimed. Search/cost storage is unchanged; no browser or live backend check was
performed. Raw observations are in [the benchmark data](42-packed-display.json).

Reproduce with:

```sh
uv run --offline --no-sync python -m runmap.experiments.benchmark_display \
  data/paths/prepared/leppavaara-flat-10k/graph.json /tmp/packed-display.json
```

The spatial benchmark's CLI was also corrected to keep its parsed output path
separate from tile arguments. Remaining node/segment metadata compaction and
SQLite prepared-feature persistence stay in the [roadmap](../../roadmap.md).

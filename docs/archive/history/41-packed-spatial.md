# Packed spatial candidate rows — 2026-10-22

Spatial cells now retain read-only int64 interval-index buffers instead of Python
lists and integer objects. The building-access-filtered view shares unchanged
rows with the complete index. Changed rows own separate immutable buffers.
Cell keys, interval numbering, duplicates, ordering, and empty rows are preserved.
Candidate queries still return ordinary sorted, deduplicated Python indices.
Preparation releases the original lists before hill construction.

## Measurements and validation

On `leppavaara-flat-10k`, with 278,839 intervals and 535 spatial cells:

| Retained spatial indexes | Bytes |
| --- | ---: |
| Previous list layout | 12,664,544 |
| Packed rows with policy sharing | 4,310,576 |

This saves 8,353,968 bytes, approximately **66.0%**. Of the filtered rows, 194
share their complete-index buffer. Recursive `sys.getsizeof` accounting uses one
visited-object set across both indexes and includes buffer owners. The reference
reconstructs the previous lists using one shared Python integer per feature;
other dataset data is excluded. These are retained subsystem sizes, not RSS or
startup peak measurements.

Eight candidate-query and full viewport comparisons matched exactly: two
locations, zooms 13/15, and building-access filtering enabled/disabled. Sixteen
static gradient tiles were byte-identical across the same views and all/surfaced
policies. All **233 tests pass**, including detached input, read-only buffers,
empty rows, duplicate/order preservation, and filtered-row sharing checks.

Twenty alternating query timings per case show a small cost from constructing
Python integers when reading packed rows. Zoom-13 medians rose from 0.95–1.65 ms
to 1.18–1.97 ms; zoom-15 medians rose from 0.19–0.24 ms to 0.22–0.29 ms.
These isolate candidate queries, not complete tile, search, or browser latency.
No routing search representation changed. The backend was not started.

Raw observations are in [the benchmark data](41-packed-spatial.json). Reproduce:

```sh
uv run --offline --no-sync python -m runmap.experiments.benchmark_spatial \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  /tmp/packed-spatial.json
```

Remaining node/segment and display metadata packing, possible interval/segment
geometry sharing, and prepared-array persistence remain in the
[roadmap](../../roadmap.md). This increment does not establish capital-area
whole-process savings or resolve JSON parsing and index rebuilding at startup.

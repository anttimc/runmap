# Packed local SQLite networks

`paths/local_network.py` now converts a projected SQLite window into the existing
packed graph, measurement, and directional hill stores. Original dataset IDs and
segment chainages remain stable; numeric indices are local to each window.
Policy permissions/rejection reasons, route-boundary blocking, building-access
classification evidence, and hill features come from whole-dataset preparation.
The loader preserves the global heuristic distance scale and separately exposes
unloaded frontier connections and source coverage bands.

The API still uses its lazy full-JSON search bridge. The next integration chooses
safe exploration envelopes and bounds request/snapshot memory; planner retries
follow. This change provides the loader and verifies search primitives without
claiming that an arbitrary query box contains a complete route.

## Validation

Seven new tests compare local and reference policy reasons/directions, shortest
paths, distance searches, partial start overlays, and full/partial directional
hill costs. They exercise crossing segments with endpoints outside the box,
expansion, local dense indices, true dead ends, unloaded connections, route walls,
one-way paths, node access, source coverage edges, globally classified driveway
branches, missing measurements, topology-only exports, and empty windows.
Source JSON reads and contextual classification are forbidden during one cropped
loader test. Numeric buffers remain read-only and share their local ID ordering.
The complete Python suite passes: 270 tests (`uv run --extra web python -m unittest discover -s tests`).

## Real extract probe

Reproduce with:

```sh
uv run --extra web python -m runmap.experiments.benchmark_database \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  --bounds 374750 6674750 375250 6675250 --local \
  --report docs/archive/history/46-sqlite-local-network.json
```

The source contains 55,627 nodes, 69,707 segments, and 278,839 measurement
intervals. Export remains an offline whole-dataset operation: 24.73 seconds,
452.8 MiB SQLite output, and about 1,013 MiB peak exporter RSS in this observation.

| Selected box | Nodes | Segments | Samples | Load | Peak reader RSS | 1 km search |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 500 m square | 114 | 124 | 832 | 34.3 ms | 78.5 MiB | 0.061 ms |
| 1.5 km square | 1,437 | 1,844 | 8,652 | 179.9 ms | 135.5 MiB | 0.605 ms |
| 3 km square | 5,735 | 7,119 | 33,148 | 680.1 ms | 272.6 MiB | 0.648 ms |

Each window runs in a separate fresh reader process. Load includes opening the
database, querying complete topology/samples/prepared features, and packing.
RSS includes Python/library imports, SQLite cache, and temporary decode objects.
The OS file cache is warm after export. Searches start at the loaded node nearest
the box centre and can reach unloaded connections; these timings do not establish
route completeness, HTTP latency, concurrency capacity, or cold-cache performance.
The observations demonstrate area-dependent loading on one extract; temporary
JSON/feature decoding remains a memory/latency optimization opportunity.

Raw measurements are in [46-sqlite-local-network.json](46-sqlite-local-network.json).
Current behavior and remaining work are documented in
[architecture](../../architecture.md#sqlite-storage-foundation),
[development](../../development.md#export-a-sqlite-network-dataset), and
[roadmap](../../roadmap.md#next-sqlite-storage-and-bounded-network-loading).

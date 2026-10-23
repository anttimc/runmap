# Zoomed-out distance tiles

Measured on 2026-10-23 on macOS ARM64 with Python 3.14.6 and the optimized native
extension. [The report](52-distance-tiles.json) records dataset/feature identities,
same-snapshot comparisons, raised-limit examples, and a local HTTP check.

## Findings

The representative 2 km snapshot prepared in approximately 108 ms, while one
fresh zoom-13 distance tile took approximately 602 ms and a zoom-12 tile took
2,232 ms. Gradient tiles were similarly expensive. The status was waiting for
display processing rather than the distance search.

A zoom-13 tile selected 27,662 intervals and performed 161,862 node-distance
lookups. Profiling attributed substantial time to repeated endpoint lookup,
display-row SQL/JSON decoding, Shapely operations, and Python MVT encoding.
These profile timings include instrumentation overhead.

## Changes

- Resolve numeric endpoint distances once per physical segment and compute only
  the inclusion/midpoint values needed by a tile. Inspection retains full
  direction/minimum records. Segments with no reachable anchor skip distance work.
- Cache compact immutable tile inputs independently of snapshots. Geometry,
  indices, chainage, grade/flat values, and flags use sealed buffers; policy and
  endpoint metadata are stored once per segment. The default byte budget is
  64 MiB, with TTL expiry and in-flight coalescing. Oversized inputs render
  without retention. Leases are released before rendering or waiting on peers.
- Encode already clipped, simplified, rounded line features with a native MVT
  writer. Projection and geometry refinement remain in Python/Shapely. The native
  writer preserves typed properties, multipart lines, and duplicate-point rules,
  and releases the GIL during serialization. It is not a general polygon encoder.
- Retain the current browser vector source when its tile template is unchanged,
  avoiding worker/source churn on unrelated refreshes. A failed source can still
  be replaced to retry it.

No source database or numeric artifact rebuild is needed. Rebuild the native
extension and restart the API; rebuild/refresh the frontend for source reuse.

## Same-snapshot tile comparisons

The baseline is commit `3d38515`. Each pair uses the same snapshot, policy,
visibility, and XYZ. Every optimized response below was a new encoded tile;
encoded-tile cache hits are excluded. Subsequent budgets reuse only the fixed
tile inputs. Decoded geometry and all properties matched the baseline exactly.

| Zoom | Budget | Before | After | Compact inputs |
| --- | ---: | ---: | ---: | --- |
| 13 | 2 km | 923 ms | 414 ms | First preparation |
| 13 | 3 km | 512 ms | 178 ms | Reused |
| 13 | 4 km | 518 ms | 169 ms | Reused |
| 13 | 5 km | 512 ms | 178 ms | Reused |
| 12 | 2 km | 2,794 ms | 2,049 ms | First preparation |
| 12 | 3 km | 2,052 ms | 651 ms | Reused |
| 12 | 4 km | 2,217 ms | 652 ms | Reused |
| 12 | 5 km | 2,447 ms | 693 ms | Reused |

The two compact input entries retained about 34.2 MB, including buffers and
metadata. Separate earlier alternating static/distance comparisons also matched
decoded tiles and showed approximately 27% faster uncached distance rendering
before compact input reuse was added.

Legacy rendering ran first in each final pair; OS/database cache state was
uncontrolled. These are small local diagnostics, not production percentiles or
browser frame measurements. A separate local HTTP check produced a 5 km snapshot
in 280 ms and the first zoom-12 tile in 2,995 ms. Its repeated encoded-tile hit
took 1.6 ms HTTP time and 0.01 ms server compute. First-load SQL/JSON preparation
therefore remains a material cost; warm input results must not be presented as
cold-load or end-to-end browser guarantees.

## Larger exploration limits

Defaults increased from 50,000 to 650,000 source segment candidates and from
2,500 to 10,000 km². Startup reads defaults from `ExplorationLimits`, retaining
environment overrides. The preparation deadline stays 10 seconds and retries
stay at two. The raised limits do not expand the prepared dataset's coverage.

At `(24.795, 60.216)`, the old segment cap rejected each example below. All
prepared successfully with the new defaults and the same numeric artifact:

| Forward budget | Selected segments | Reached nodes | Preparation |
| --- | ---: | ---: | ---: |
| 5 km | 103,941 | 42,467 | 419 ms |
| 10 km | 324,789 | 162,141 | 934 ms |
| 20 km | 561,529 | 346,111 | 1,086 ms |

## Validation and reproduction

All 348 Python tests pass, including native/reference decoded linework and typed
properties, snapshot-independent cache reuse, bounded/oversized inputs, immutable
buffers, policy/budget equivalence, and parallel rendering with one database
lease. Five Rust tests and Clippy checks pass, including Python bindings.
Frontend build and three transport tests pass.

Raw profiles, samples, and local reproduction scripts are retained under
`artifacts/pathfinding-profile/distance-tiles/`. The committed baseline modules
used for comparison were read with `git show 3d38515:<module-path>` and loaded
only in the measurement process. No running API functions were patched.

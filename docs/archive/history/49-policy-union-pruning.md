# Numeric policy-union pruning

Measured on 2026-10-23 with macOS 26.6.2 ARM64 and Python 3.14.6.
Dataset and feature identities, request endpoints, and measurements are retained
in [the report](49-policy-union-pruning.json).

## Change

Numeric export retains physical segments allowed in at least one direction by
at least one profile declared in metadata. Missing policy rows fail export;
absence is not interpreted as a confirmed restriction. Original numeric IDs and
the source network remain intact. Nodes without retained segments, empty chunks,
and excluded segments' arcs, geometry, and hill arrays are omitted. Recomputed
global incidence and chunk envelopes prevent excluded links from becoming false
unloaded frontiers. Segments supported only when stairs are allowed remain.

The binary layout stays version 2. The additional JSON metadata value
`numeric_preprocessing_version` records `"policy-union-v1"`. Existing artifacts
remain readable; obtaining the reduction requires re-export. Maintained behavior
is documented in [the numeric format](../../numeric-routing-format.md).

## Capital-area size

| Metric | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| Physical segments | 631,979 | 586,218 | 7.2% |
| Directed arcs | 1,263,958 | 1,172,436 | 7.2% |
| Hill intervals | 2,749,906 | 2,616,596 | 4.8% |
| Numeric payload bytes | 617,942,800 | 582,072,800 | 5.8% |
| SQLite file bytes | 776,671,232 | 740,032,512 | 4.7% |
| Spatial chunks | 3,461 | 3,459 | 2 chunks |

Original chunk grouping is retained; surviving chunks are not repacked together.
Identity rows for excluded records remain available for provenance and do not
imply membership in a loaded graph.

## Equivalence and latency

Short and long complete requests with hills Off and High produced identical
route response hashes after excluding elapsed time and search-area metadata.
This compares coordinates, original traversals, distances, cost breakdowns,
observations, and expansion counts. All four cases still needed two windows.

Twenty-four distance comparisons covered all four profiles, forward/reverse
search, and 1/3/5 km budgets in the same bounded working area. The start was
snapped under each profile. Reached global node IDs and distances matched exactly.
This is bounded-search equivalence, not a full browser or exploration-delivery
performance measurement.

| Complete request | Before repeated median | After repeated median |
| --- | ---: | ---: |
| Short, hills Off | 33.3 ms | 31.0 ms |
| Short, hills High | 41.5 ms | 41.6 ms |
| Long, hills Off | 2,643 ms | 2,571 ms |
| Long, hills High | 2,764 ms | 2,755 ms |

Each case used a fresh application graph cache, one first request, and three
repeated requests per artifact. Timings include handler execution and JSON
serialization but exclude HTTP/browser work. OS cache was uncontrolled and the
old artifact was measured first; first-request differences cannot establish
cold-disk improvements. These small samples show modest or negligible warm
latency changes, not production percentiles. Large payloads, exact-bounds cache
keys, SQL safety counts, and repeated window loading remain bottlenecks.

Local raw phase samples and the comparison script are under
`artifacts/pathfinding-profile/policy-union/`. Reproduce locally with:

```sh
uv run python artifacts/pathfinding-profile/policy-union/benchmark.py
```

The filtered artifact was selected at the default local
`numeric-routing-v2.sqlite` path, with the original preserved at
`numeric-routing-v2-before-policy-union.sqlite`. Running API processes must
restart to open the new generation.

## Checks

All 338 Python tests pass. Added regression coverage checks omitted terminal
nodes and empty chunks, shrinking envelopes, exclusion of unsupported policy
rows, missing-policy failures, false-frontier removal, and preservation of real
cross-window frontiers. Native low-level endpoints referring to omitted nodes
are rejected as outside the loaded graph; API snapping already requires an
eligible segment.

# Integer planner costs — 2026-10-21

Following the [packed-hill migration](35-packed-hill-storage.md), the planner now
passes prepared integer arcs and integer node IDs through cost and heuristic
callbacks. Current ownership and compatibility boundaries are in
[architecture](../../architecture.md); remaining work is in the
[shared-index roadmap](../../roadmap.md#next-shared-integer-indexing-and-compact-prepared-data).

## Scope

Shape caches use overlay segment indices, hill caches use directed arc indices,
and return-leg reuse sets use segment indices. Endpoint part records contain
the original physical segment index and ascending graph chainage. Selected
traversals convert to external IDs for response serialization.

Integer arcs retain physical direction and have an aligned external-ID table.
Projected geometry adds aligned reference tuples sharing its existing point
objects; endpoint overlays share base tuples and append only their new geometry.
Raw geometry maps and JSON records remain for preparation, inspection, generic
record-callback searches, and the not-yet-migrated exploration/display pipeline.
Heap tie keys preserve external node names so equal-cost routes keep their order.
There is no whole-network request-cache allocation or geometry scan per request.

The retained string-search benchmark now prepares integer feature IDs for both
planners and adapts indexed callbacks at its boundary. Its current complete-route
results cannot be compared directly with the earlier report that skipped integer
overlay preparation for the string planner.

## Validation

All 216 Python tests pass. A guard test removes JSON arc records, string arc and
segment mappings, and string geometry maps before running indexed search/cost
evaluation, covering shape Off/On and hill Off/Low/High. Additional checks cover
integer cache keys, shared coordinate references, physical direction, invalid
indexed costs/heuristics, callback ambiguity, and existing policy, deadline,
endpoint split, reuse, and external-response behavior.

Complete planners were compared against commit `fd50d7e` on the real
`leppavaara-flat-10k` dataset, identity
`b74d86e5dcebdb20802ec502ed024214d8950f46fa2b0d7c9c589188d028d81d`.
The reference search, cost, request-data, and planner modules were loaded from
that commit. Both sides used the same prepared graph, packed hills, geometry,
and native integration kernel. A preparation-only adapter supplies physical
segment records to the updated integer-overlay builder for the old planner.

All 18 cases matched exactly: short, profile, and longer endpoints; zero/default
shape width; Off/Low/High hills. Comparison included paths, costs, observations,
physical traversals, expanded-node counts, and outcomes. Each timing case used
10 paired repetitions with alternating order and fresh request caches, after
warm-up/equivalence checks. The existing integer-search benchmark also completed
with one repetition across its scenarios and Python integration reference.

Representative complete-planner medians with default shape settings:

| Case | Hills | Before ms | Indexed ms |
| --- | --- | ---: | ---: |
| Profile | Off | 48.2 | 40.4 |
| Profile | Low | 173.4 | 151.9 |
| Profile | High | 234.0 | 210.8 |
| Longer | Off | 129.8 | 108.6 |
| Longer | Low | 430.3 | 379.6 |
| Longer | High | 548.5 | 474.4 |

These medians improved by about 10–16%. The short shape-off/hills-off case changed
very little. Python 3.14.6, macOS ARM64; shared development-machine activity and
garbage collection remained enabled. A separate short benchmark smoke check
overlapped part of this run, and p95/max observations include large outliers on
both sides. These are diagnostic timings, not isolated latency targets or proof
of browser responsiveness, startup improvements, or capital-area tail latency.
Detailed settings and observations are in the [comparison JSON](36-indexed-planner-costs.json).

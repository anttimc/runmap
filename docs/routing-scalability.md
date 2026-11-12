# Routing scalability and route reshaping

Design direction reviewed 2026-11-12. These are proposals, not implemented
features or measured improvements. Outstanding tasks live in the [roadmap](roadmap.md).

## Evidence and current boundaries

Responsiveness is the first product priority. Distinguish longer requested routes
from larger prepared coverage: a local run should not load a national graph.

Rust's `chunk_search.rs` currently uses a scaled straight-line lower bound,
including an endpoint-overlay safety adjustment. Signal preferences introduce
arrival-movement labels. Python's `api/sqlite_runs.py` selects rectangular working
areas and retries with larger areas based on frontier and route-boundary checks.
This expansion is heuristic, not a proof of global optimality.

`api/numeric_data.py` caches whole working areas by bounds. An oversized graph
bypasses the cache; changed bounds cannot reuse its chunks through this cache.
Configured environment data is attached during graph loading even with preferences
Off. The [2026-11-11 profile](archive/history/2026-11-11-distance-search-profile.md)
records about 9 ms planning but seconds of preparation on a 19.6 km distance-only
round trip. The [longer-run measurements](100ms-routing-architecture.md#longer-run-validation)
also show significant search time for shaped routes. Neither establishes the
bottleneck of a new request without measuring it.

## Proposed sequence

1. Capture representative longer runs with actual preferences, separating loading,
   search, retries, serialization, and browser latency. Include moved endpoints,
   cold/warm data, concurrent requests, and increasing coverage at fixed route size.
2. Reuse immutable chunks across overlapping areas with byte-bounded residency.
   Separate topology from optional cost data; avoid full-area environment loading
   when unused while preserving selected-route diagnostics. Measure cold requests
   as well as warm reuse; increasing the whole-graph cache alone is insufficient.
3. Prototype landmark lower bounds in the existing Rust search and compare equal
   requests against the current heuristic and reference results.
4. Evaluate bound-driven area expansion or incremental chunk loading, so reducing
   search work also reduces preparation. Preserve source-edge and unloaded-edge
   distinctions, deadlines, and explicit outcomes when limits prevent completion.

## Landmark experiment

Goldberg and Harrelson's [2004 report](https://www.microsoft.com/en-us/research/wp-content/uploads/2004/07/tr-2004-24.pdf)
and [readable conference version](https://www.microsoft.com/en-us/research/wp-content/uploads/2005/01/soda05.pdf)
describe ALT: precompute directed distances to and from landmarks, then derive
A* lower bounds using triangle inequalities. Landmark selection and bidirectional
search are also investigated there.

Proposed Runmap adaptation: prepare a distance-only relaxed graph whose arc costs
are no greater than any supported request cost and whose connectivity includes
all permitted request traversals. Additional restrictions and nonnegative costs
then retain the lower-bound property. Confirm this invariant against every cost
term and overlay, rather than assuming preference-specific tables can be reused.

For node v, target t, and landmark L, use valid finite terms from
`max(0, d(L,t) - d(L,v), d(v,L) - d(t,L))`, maximized across landmarks.
Combine with the existing safe geometric bound. Handle disconnected components,
directed access, partial endpoint arcs, and numerical rounding explicitly;
never subtract infinities. Prove consistency on query overlays and signal states.
Distance-only bounds can remain weak when shape or preference penalties dominate.

Store tables against stable global node identifiers and dataset/topology/cost
identities, with bounded row access for loaded nodes. Preprocess over full prepared
connectivity, not arbitrary request windows. Measure table size, preprocessing,
I/O, heuristic evaluation cost, and expanded states as well as complete latency.
Tables alone do not remove rectangular loading or prove that an unloaded detour
cannot improve the route. Such a proof needs valid bounds covering every possible
exit and continuation compared with a feasible complete-route cost.

Bidirectional ALT is a later comparison, requiring correct termination and reverse
movement-cost semantics. A hierarchical routing design is another later candidate
if profiling still demands it; request-dependent shape, reuse, and preference
costs make reusable shortcut costs a separate design problem. Neither is committed.
Target-directed ALT does not directly accelerate all-destinations distance shading.

## Dragging a route to a pin

The next route-shaping direction is to drag a point on the displayed route to a
new pinned location and reroute through it. Preserve traversal order, including
which occurrence was dragged on repeated stretches. Provide pin movement/removal
and an equivalent keyboard/touch interaction. This is not implemented.

The amount of permitted change needs a product decision. Distinct choices are:

- Preserve geometry: retain unaffected sections or penalize departure from the
  previous route, allowing a local edit around the pin.
- Limit extra length: constrain added metres or a percentage relative to the
  original route.
- Limit preference cost: constrain deterioration under the original objective.

These are not interchangeable. A geographically different route may have equal
length and cost. A required pin may make a strict budget infeasible. Keep the
original objective/settings fixed for comparisons; a route-stability penalty is
an additional objective, not the same metric. Decide whether limits are hard or
advisory and how infeasibility is shown before implementing them.

For round trips, independently optimizing legs through pins can change reuse
penalties and signal transitions. Preserve those cross-leg semantics and define
whether the original shape field stays fixed. Exactness of individual A* searches
does not establish global optimality of the complete loop construction.

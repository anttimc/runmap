# Routing scalability and route reshaping

Design direction reviewed 2026-11-12. Shared chunk reuse is implemented as
described below; the broader boundaries and experiments remain proposals.
Outstanding tasks live in the [roadmap](roadmap.md).

## Interaction target

Support runs up to 40 km with a revised route visible within one second after a
settings change or release of a dragged route pin. This is the user-agreed target,
not current measured performance. It includes scheduling, server work, transport,
and browser rendering, not just the shortest-path kernel. Pin reshaping itself
remains unimplemented; its detailed interaction design is deferred while scaling
is the focus.

Measure warm edits, first requests in a new area, and process-cold requests
separately. Do not hide a cold-area miss behind a warm-only claim. Hardware,
concurrency, network conditions, and the acceptance percentile still need to be
fixed before declaring the target met. The existing 100 ms backend target remains
a supporting engineering aspiration, not a substitute for this interaction target.

## Proposed architectural boundaries

Keep Python preparation/API delivery and in-process Rust search. The first changes
should concern data ownership and request lifetime; a separate routing service
is not required to test this design.

| Boundary | Proposed responsibility |
| --- | --- |
| Offline preparation | Publish versioned topology, packed preference data, geometry, and landmark tables with matching identities |
| Shared graph store | Own immutable chunks, stable identifiers, cross-chunk adjacency, bounded residency, and coordinated loading |
| Per-request Rust search | Own endpoint/pin overlays, costs, labels, queue, deadline, and cancellation; borrow shared graph data |
| API orchestration | Validate requests, admit bounded work, assemble selected-route results, and report timing/limit outcomes |
| Browser coordinator | Submit the latest settings revision, supersede obsolete work, and measure time until the route is visible |

**Replace whole-area reconstruction with shared chunks.** Cache reusable decoded
topology/index data as well as payload bytes. Request-local node remapping must
not force rebuilding the entire adjacency index on every area change. Give each
dataset generation separate ownership; hold borrowed chunks alive until its
active searches finish. Bound both retained cache and in-flight working memory.

**Separate topology, prepared inputs, and display geometry.** Hills, mapped
environment fields, and signals share the [prepared input boundary](architecture.md#prepared-routing-inputs).
Prepare all three in compact chunk-addressable form to reduce per-request SQL/JSON/Python
conversion. Attach only required cost channels to searches; load diagnostics and
display geometry for selected traversals. Preserve geometry needed for exact shape
integration and snapping: display-only deferral cannot remove those inputs.
Preference edits should reuse prepared inputs while replacing query-local weights.

**Let search bounds influence data loading.** Initially reuse chunks across the
existing window retries. Subsequently evaluate incremental region expansion with
valid frontier bounds, avoiding repeated full searches where state can safely be
retained. Define how the search requests missing chunks without per-edge Python
callbacks. Landmark rows should follow the same bounded data-access design.

**Make superseded work stop.** Browser response rejection currently protects the
display but does not immediately stop native work. Add request revisions and
cooperative cancellation through preparation and Rust traversal. Use bounded
admission and coalesce superseded pending edits per client without cancelling
other users' work. Coordinate loads of the same chunk once; avoid a global lock
around unrelated preparation. Cancellation and load failures must release leases.

**Keep output work proportional to the selected route.** Batch selected-segment
reads, reuse decoded geometry, and measure response construction and rendering.
Keep elevation/export preparation outside the route-update critical path where
the UI does not require it. Include any debounce delay in the one-second budget.

First prove this on one machine with a documented memory budget. For hosting,
publish immutable prepared datasets separately from serving, retain local routing
data on workers, and route requests to workers with reusable regional data.
Multiple workers currently need an explicit policy for process-local exploration
snapshots and duplicated caches. Replicas help concurrent users; they do not by
themselves make a slow single query meet the target. National coverage must be
tested with fixed local requests and bounded active memory before public scaling.

## Evidence and current boundaries

Responsiveness is the first product priority. Distinguish longer requested routes
from larger prepared coverage: a local run should not load a national graph.

Rust's `chunk_search.rs` currently uses a scaled straight-line lower bound,
including an endpoint-overlay safety adjustment. Signal preferences introduce
arrival-movement labels. Python's `api/sqlite_runs.py` selects rectangular working
areas and retries with larger areas based on frontier and route-boundary checks.
This expansion is heuristic, not a proof of global optimality.

`api/numeric_data.py` retains exact-area graphs and a byte-bounded
`routing/numeric_store.py` shared chunk LRU. New bounds select chunk identities
and fetch/prepare only misses. Rust retains immutable payloads, validated
chunk-local adjacency, sorted node/arc identities, segment lengths, and safe
geometric scales. Active graphs retain native owners after eviction or shutdown.
New areas still merge node indexes and rebuild selection/frontier state; a single
preparation lock still serializes unrelated loads. Enabled environment/signal
search data remains attached per area; inactive channels skip those loads.
Overlapping-window, concurrent-preparation, eviction, deadline,
and route-equivalence tests cover reuse. The
[November 12 measurements](archive/history/2026-11-12-shared-store-measurements.md)
cover long-route setting/endpoint edits, cache controls, and two concurrent
requests. They identify full-area optional-input attachment as the largest cold
cost and about 2.1 s of planning for a fully cached 39.63 km mixed-preference
request. Full interaction acceptance remains outstanding. An oversized graph
bypasses exact-area retention but may reuse chunks
that fit the shared cache. See [cache accounting](development.md#export-a-sqlite-network-dataset).
Zero-weight environment observations now load only returned-route detail after
window selection. The earlier unconditional attachment is preserved in the dated
measurements. The [conditional-input follow-up](archive/history/2026-11-12-conditional-routing-inputs.md)
checks response equality and measures the resulting performance. The
[2026-11-11 profile](archive/history/2026-11-11-distance-search-profile.md)
records about 9 ms planning but seconds of preparation on a 19.6 km distance-only
round trip. The [longer-run measurements](100ms-routing-architecture.md#longer-run-validation)
also show significant search time for shaped routes. Neither establishes the
bottleneck of a new request without measuring it.

## Proposed sequence

1. Capture representative longer runs with actual preferences, separating loading,
   search, retries, serialization, and browser latency. Include moved endpoints,
   cold/warm data, concurrent requests, and increasing coverage at fixed route size.
2. Build on the implemented shared chunk ownership and byte-bounded reuse.
   Reduce area-wide index assembly and unrelated-load serialization.
   Pack and reuse the common hill/environment/signal input channels. Inactive
   environment/signal search loading and selected-route zero-weight diagnostics
   are implemented; active channels still need reusable packed storage. Measure cold requests
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

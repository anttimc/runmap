# Routing scalability and route reshaping

Runmap needs to stay responsive as routes get longer and prepared coverage grows.
The main approach is to reuse prepared data between requests, then reduce the
work needed to search it. Shared graph chunks and optional packed preference
inputs are implemented. Further loading improvements, landmarks, and route pins
remain proposals. Outstanding tasks live in the [roadmap](roadmap.md).

The design direction was reviewed on 2026-11-12. The [glossary](glossary.md)
explains the network, storage, and search terms used below.

## Interaction target

For runs up to 40 km, aim to show a revised route within one second of a settings
change or release of a dragged route pin. This is the user-agreed target, not
current measured performance. The second includes waiting to submit work, server
processing, network transfer, and browser drawing. Route pins are not implemented;
their detailed interaction design is deferred while scaling is the focus.

Measure edits using cached data, first requests in a new area, and first requests
after server startup separately. Before declaring success, choose the hardware,
number of simultaneous requests, network conditions, and latency percentile to
use. The 100 ms server target supports this goal but does not replace it.

## Proposed architectural boundaries

Keep preparation and API delivery in Python, with Rust search inside the same
process. Start by improving which data is shared and how long it stays available.
These changes can be tested without creating a separate routing service.

| Part | Proposed responsibility |
| --- | --- |
| Offline preparation | Publish matching versions of connections, preference observations, path geometry, and proposed landmark tables |
| Shared graph store | Keep read-only chunks and connection indexes, coordinate loads, and limit cached bytes |
| Per-request Rust search | Keep temporary endpoint/pin connections, costs, best-known paths, queue, deadline, and cancellation state; read shared graph data |
| API orchestration | Validate requests, limit active work, build selected-route results, and report timings and limits |
| Browser coordinator | Submit the latest settings, replace outdated requests, and measure time until the route is drawn |

A request should first reuse as much prepared data as possible. Shared chunks
already avoid repeated decoding and validation. The next step is to avoid
rebuilding all connection indexes whenever the search area changes. Keep data
from different dataset versions separate. Active searches must keep their chunks
alive, even after cache eviction. Limit temporary working memory as well as caches.

Next, load only the preference inputs a search needs. Hills, environment, and
signals already share a [preparation interface](architecture.md#prepared-routing-inputs)
and can use compact files organized by chunk. Changing a preference weight should
reuse the observations. Read route diagnostics and display geometry only for the
selected route where possible. Search still needs geometry for snapping and exact
shape-cost calculations; that geometry cannot be deferred until display.

Data loading should eventually follow the search's needs. Start with the existing
retries over larger windows, reusing chunks between attempts. Then test whether
search can expand its area and safely keep earlier work. This requires valid
cost bounds at the boundary. Rust must be able to obtain missing chunks without
calling Python for every edge. Proposed landmark data needs the same loading and
memory limits.

Outdated requests should stop doing work. Today, the browser ignores old responses,
but this does not immediately stop Rust. Add request versions and cancellation
checks during preparation and traversal. Limit active work and keep only the
latest pending edit for each client, without cancelling other users' requests.
Load a shared chunk once when several requests need it, while allowing unrelated
loads to proceed. Release reserved resources after cancellation or load failure.

Finally, keep result construction proportional to the selected route. Read its
segments in batches, reuse decoded geometry, and measure response building and
browser drawing. Prepare elevation and exports separately where the route display
does not need them. Any delay used to group rapid edits counts toward the second.

Prove the design on one machine with a documented memory budget before scaling
hosting. Publish prepared datasets separately from the serving process. Keep
routing data local to workers and send requests to workers likely to have the
needed regional data. Multiple workers need a policy for their separate caches
and exploration snapshots. More workers help simultaneous users, but do not
necessarily speed up one request. Before public scaling, increase prepared
coverage while keeping local requests fixed and check that memory stays bounded.

## Evidence and current boundaries

A longer route and a larger prepared dataset create different challenges. A local
run should load local data even if the prepared dataset covers all of Finland.

Today, `api/sqlite_runs.py` selects a rectangular search area. It checks reached
boundaries and the returned route, then may retry with a larger rectangle. These
checks are heuristic: they do not prove that no better route exists outside the
loaded area. Rust's `chunk_search.rs` guides search with a scaled straight-line
lower bound, adjusted for temporary endpoint connections. When signals are
enabled, search also remembers how it arrived at a node so it can charge the
correct crossing movement.

`api/numeric_data.py` caches exact-area graphs and uses the byte-limited shared
chunk cache in `routing/numeric_store.py`. New areas load and prepare only missing
chunks. Rust retains validated connections, sorted node and arc IDs, segment
lengths, and safe scales for the geometric lower bound. Active graphs keep these
read-only buffers alive after eviction or shutdown.

Each new area still merges node indexes and marks selected paths and connections
beyond the boundary. A single preparation lock still makes unrelated loads wait.
With the optional [packed input file](numeric-routing-format.md#packed-routing-inputs),
overlapping graphs share validated input chunks, environment interval arrays,
and hill-total arrays. Rust addresses hill totals directly by chunk and segment.
Packed topology buffers omit the older hill columns. A format-3 conversion also
removes duplicated hills from disk, leaving them in the paired compressed input
file. Environment and signal lookups are still assembled for each area. Without
packed inputs, enabled channels use the older per-area loading path.

Tests cover overlapping areas, concurrent preparation, eviction, deadlines, and
route equivalence. Graphs too large for the exact-area cache can still reuse
chunks that fit the shared cache. See [cache accounting](development.md#export-a-sqlite-network-dataset)
for what the budgets include.

The dated measurements show why both preparation and search matter:

- The [November 12 report](archive/history/2026-11-12-shared-store-measurements.md)
  covers longer-route edits, cache sizes, and two simultaneous requests. Loading
  optional inputs for the whole area was the largest uncached cost. A fully
  cached 39.63 km mixed-preference request still spent about 2.1 s planning.
- The [conditional-input follow-up](archive/history/2026-11-12-conditional-routing-inputs.md)
  checks response equality and measures the change to load zero-weight environment
  observations only for the returned route. The earlier report predates this change.
- The [November 11 distance-only profile](archive/history/2026-11-11-distance-search-profile.md)
  records about 9 ms planning but seconds of preparation for a 19.6 km round trip.
  The [longer-run measurements](100ms-routing-architecture.md#longer-run-validation)
  also show substantial search time when shape costs are enabled.

These workloads expose different costs. Measure a new request before choosing
what to optimize. Full browser interaction acceptance remains outstanding.

## Proposed sequence

1. Measure representative longer runs with real preferences. Separate loading,
   search, retries, response serialization, and browser latency. Include moved
   endpoints, cached and uncached data, simultaneous requests, and larger coverage
   at a fixed route size.
2. Build on shared graph chunks and packed preference inputs. Reduce index assembly
   for each area and avoid making unrelated loads wait. Measure first loads as
   well as reuse; a larger whole-graph cache alone will not resolve these costs.
3. Test landmark lower bounds in Rust. Compare the same requests with the current
   heuristic and Python reference, including full latency and memory.
4. Test expansion guided by cost bounds or loading chunks as needed. Keep true
   source boundaries distinct from unloaded connections, preserve deadlines, and
   report when limits prevent completion.

## Landmark experiment

The proposed ALT experiment would prepare distances to and from selected nodes,
called landmarks. Triangle inequalities then give a lower bound on the distance
still to travel. The approach is described in Goldberg and Harrelson's
[2004 report](https://www.microsoft.com/en-us/research/wp-content/uploads/2004/07/tr-2004-24.pdf)
and [conference version](https://www.microsoft.com/en-us/research/wp-content/uploads/2005/01/soda05.pdf).
The experiment is not implemented in Runmap.

For Runmap, prepare a distance-only graph that includes every connection a
supported request could use. Its arc costs must never exceed the corresponding
request costs. This is the relaxed graph: requests may add restrictions or
nonnegative penalties without invalidating its lower bounds. Check the condition
for every cost term and temporary endpoint connection.

For node v, target t, and landmark L, take valid finite terms from
`max(0, d(L,t) - d(L,v), d(v,L) - d(t,L))`, then take the maximum across landmarks.
Combine it with the existing safe geometric bound. Handle disconnected paths,
directed access, partial endpoint arcs, and rounding explicitly; never subtract
infinities. Prove consistency for endpoint overlays and signal search states.
A distance-only bound may give little help when preference or shape penalties
dominate the total cost.

Store tables by stable global node ID, with identities for the dataset, topology,
and cost model. Prepare them over the full network, not a request rectangle, and
limit the rows loaded at runtime. Measure table size, preparation and read time,
bound-evaluation time, expanded search states, and full request latency.

Landmark tables alone neither remove rectangular loading nor prove that an
unloaded detour cannot improve a route. Such a proof needs valid bounds for every
possible exit and continuation, compared with the cost of a known complete route.

Searching from both ends with ALT is a later option. It would need correct stopping
rules and reverse crossing costs. Hierarchical routing is another option if
measurements justify it, but reusable shortcuts are harder when shape, preference,
and reuse costs change per request. Neither option is committed. A target-directed
landmark search also does not directly speed up distance shading to all destinations.

## Dragging a route to a pin

The next route-shaping direction is to drag a point on a displayed route to a new
pin and reroute through it. This is not implemented. Preserve travel order,
including which occurrence was dragged when the route uses a stretch more than
once. Support moving and removing pins, with keyboard and touch equivalents.

First decide how much the route may change:

- Preserve its shape by keeping unaffected sections or penalizing departure from
  the previous route.
- Limit added length, in metres or as a percentage of the original route.
- Limit the increase in cost under the original preferences.

These choices produce different results. A very different route may have the same
length and cost, and a required pin may make a strict budget impossible. Compare
routes using the original settings. A new penalty for leaving the old route is an
extra objective. Decide whether limits are required or advisory, and what to show
when no route can meet them.

For round trips, changing one leg can affect reuse penalties and signal movements
across legs. Preserve those rules and decide whether the original shape field
stays fixed. Finding the best path for each individual search does not prove that
the complete loop is globally best.

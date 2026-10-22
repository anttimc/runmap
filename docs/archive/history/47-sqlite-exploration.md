# SQLite exploration API

Exploration, snapshots, and inspection with start-distance information now use
bounded local SQLite topology. They work without the source JSON and never invoke
the full-graph bridge. Run planning still uses that bridge.

## Search and ownership

The initial projected box encloses the complete snapped segment and expands each
side by the forward budget divided by the saved global endpoint-distance scale,
plus one metre for numeric tolerance. Including the entire start segment protects
partial starts and long/bent geometry; subsequent reachable arc endpoints obey
the global displacement bound. The viewport has no effect on route legality.
Local preparation skips measurements and hill records, using saved routing
permissions and boundary blocking. Display/inspection query their intervals
separately.

A reached unloaded frontier causes a fresh search in a box with double width
and height. Defaults are two retries, 2,500 km² maximum area, and 50,000 selected
segments. Area/count checks happen before local decoding. Limit exhaustion is
HTTP 422 with `status=area_limited`, a reason, attempted projected bounds, and
retry count. Deadline expiration is HTTP 504; busy connection leases remain
HTTP 503. Source coverage flags and physical dead ends keep separate meanings.

Connection leases cover local loading and search, bounding simultaneous
preparations. Lease waits and SQLite progress handlers observe the preparation
deadline, and search/packing checks share it. Identical pending snapshot work
coalesces before graph loading. The snapshot retains its routing graph and
sealed distances together, charging their shared buffer owners/maps once under
the existing byte/TTL cache budget. Evicted cache entries release both stores.
Graph-specific ID checks prevent applying distances to a different window.
Active requests may keep references after cache eviction; the cache limit is
not a whole-process RSS cap.

## Validation

Eight new tests cover viewport and reverse-inspection equality, all four policies,
three budgets, sixteen snapshot tile combinations per case, distant segments
outside the local graph, no snap, invalid budgets, area/segment/deadline limits,
forced reached-frontier retries, shared preparation, cache eviction/expiry,
graph-order mismatches, long/bent-start envelopes, SQL interruption and handler
cleanup, and deadline-bound pool waits. JSON reads are forbidden during parity
tests; preparation rejects attempts to load local sample or hill stores.
Endpoint checks include source-free exploration/inspection/snapshot preparation,
stale dataset rejection, HTTP 422 limit responses, and HTTP 504 deadlines.

The full Python suite passes: 278 tests.

## Real extract probe

```sh
uv run --extra web python -m runmap.experiments.benchmark_database \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  --bounds 374750 6674750 375250 6675250 --explore-budget-km 3 \
  --report docs/archive/history/47-sqlite-exploration.json
```

The source has 55,627 nodes, 69,707 segments, and 278,839 samples. A 3 km forward
budget selected 21,518 nodes and 26,678 segments with no frontier retries.

| Observation | SQLite | JSON reference |
| --- | ---: | ---: |
| Dataset initialization | 10.2 ms | 8,356 ms |
| First exploration preparation | 1,467 ms | 10.8 ms |
| Repeated preparation | 0.68 ms | 2.62 ms |
| First exploration viewport | 18.7 ms | 16.9 ms |
| First distance snapshot tile | 43.8 ms | 61.5 ms |
| Repeated snapshot tile | 0.017 ms | 0.009 ms |
| Snapshot cache charge | 25.9 MiB | 0.51 MiB |
| Peak reader RSS | 354.8 MiB | 941.0 MiB |

SQLite preparation includes its local graph load/packing and ownership accounting.
JSON preparation uses a graph loaded globally at initialization, with its policy
already prepared by earlier static-delivery work. JSON's smaller snapshot charge
excludes that globally retained graph; reader RSS includes it. Reader RSS includes
imports and temporary decoding buffers; each backend runs in a fresh subprocess,
separate from offline export. The OS file cache is warm after export. These are
individual direct-call observations, excluding HTTP/browser transport and
concurrent requests; they are not latency percentiles or cold-cache results.

Canonical exploration viewport hashes and exact encoded distance-tile hashes
match between readers, as do static-delivery hashes. SQLite reports
`legacy_loaded=false`. The detailed timings and checksums are in
[47-sqlite-exploration.json](47-sqlite-exploration.json).

Next is local run planning with endpoint-box expansion and one deadline across
all candidate legs/retries, followed by removing its remaining JSON bridge.

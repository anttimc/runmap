# Environment routing latency investigation — 2026-11-09

This dated diagnostic found that environment preferences can turn a small search
increase into repeated graph preparation through cache eviction. No routing
behavior, cache configuration, or running server was changed.

## Scope and evidence

Source revision: `25b4c6eae7694dc83a077a4ef7e4bddf50f78658`. Local Helsinki
dataset: `2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`.
The experiment used `network.sqlite`, `numeric-routing-v2.sqlite`, and
`environment-join.sqlite` from `data/paths/prepared/helsinki-capital-area`,
with `data/environment/prepared/helsinki-capital-area-v3` as the raster identity.

Home was `(24.795, 60.216)` and turnaround `(24.815, 60.225)`. Requests used
default round-trip settings, default surface/stair policy, and hills off.
Environment intervals were available in every workload, including preferences
off, matching the server's automatic environment-join discovery.

Measurements call the complete request handler and JSON serialization, excluding
HTTP transport and startup. Each workload starts with fresh dataset objects.
Seven repetitions follow an initial request captured with Python cProfile.
Only the repetitions in the table below are unprofiled. Filesystem caches were
uncontrolled; these are local observations, not production percentiles.

| Preferences | 128 MiB cache: median request | Median preparation | Median search | Final-attempt expanded nodes | Windows per request |
| --- | ---: | ---: | ---: | ---: | ---: |
| Off | 27.08 ms | 0.17 ms | 15.43 ms | 10,781 | 2 |
| Greenery high | 41.75 ms | 0.20 ms | 28.70 ms | 19,174 | 2 |
| Water high | 36.43 ms | 0.15 ms | 25.04 ms | 21,777 | 2 |
| All low | 31.45 ms | 0.15 ms | 20.10 ms | 16,637 | 2 |
| Traffic high | 1,936.16 ms | 1,830.44 ms | 45.80 ms | 21,001 | 3 |
| All high | 1,927.96 ms | 1,799.76 ms | 59.35 ms | 30,419 | 3 |

Phase medians do not sum to the request median. Expanded-node counts describe
the returned candidates in the final attempt; they exclude discarded attempts.

## Main hotspot: window expansion and cache eviction

[`_plan_numeric_windows`](../../../src/runmap/api/sqlite_runs.py) always starts
from the initial endpoint bounds. It doubles each window dimension when any
search reaches a frontier. In the second window, traffic high has
`reached_frontier=true` while `near_boundary=false`: search exploration itself
causes the third window, even though the selected route is away from the edge.

[`NumericRoutingData._prepare`](../../../src/runmap/api/numeric_data.py) caches
graphs by exact bounds. The three window sizes account for **13.65, 34.88, and
116.17 MiB**, totaling **164.70 MiB**. Each fits individually in the default
128 MiB cache, but the sequence does not. Loading the final graph evicts the
smaller ones. The next request loads the first window, evicts the final graph,
and eventually reloads all three. The final cache contains only the large graph.

Changing only the experiment's cache allowance to 256 MiB retains all three:

| Preferences | Median request | Median preparation | Median search |
| --- | ---: | ---: | ---: |
| Off | 27.20 ms | 0.16 ms | 15.81 ms |
| Traffic high | 66.81 ms | 0.29 ms | 47.29 ms |
| All high | 81.45 ms | 0.26 ms | 62.48 ms |

Traffic-high repeated latency falls about **29×**. Response SHA-256 fingerprints
match across cache sizes and the separate profiling build for all three
workloads. The allowance uses existing cache accounting, not measured live RSS.
A larger cache addresses repeated requests; it does not remove initial loading
or establish a suitable memory budget for every route or deployment.

## Preparation hotspot: environment interval loading and conversion

[`_attach_environment`](../../../src/runmap/routing/numeric_search.py) calls
[`read_environment_segments`](../../../src/runmap/paths/environment_join.py),
which reads SQLite rows in batches of 500 segment IDs, constructs Python
`EnvironmentInterval` records, and then converts them into nested tuples for
PyO3 to copy into a Rust hash map and interval vectors.

In a cProfile capture of the initial traffic-high request, environment attachment
accounted for about **2.46 s of 2.84 s** wall time across three windows. The
reader accounted for **1.57 s inclusive** within attachment. The capture made
about **6.6 million Python calls**. These instrumented times locate the work;
they are not unprofiled latency estimates. The repeated default-cache requests
pay this same loading/conversion path after eviction.

All selected segments are attached whenever an environment connection exists,
even if preference weights are zero. Loading happens under the preparation
lock, which can also delay other preparation requests; contention was not
measured here.

## Search hotspots: broader A* and repeated cost evaluation

[`estimate_remaining_cost`](../../../rust/src/chunk_search.rs) uses a safe
straight-line distance lower bound. Environment penalties raise accumulated
cost without strengthening that bound. More nodes and edges are explored, and
more frontiers are reached. Traffic high resolves to weight 4.0; the other
environment high settings resolve to 1.0.

A separate optimized build with the existing `profiling` feature measured five
repetitions at 256 MiB. The exclusive native cost phase includes environment
integration, cost-cache lookups, component assembly, and validation; it does not
isolate environment arithmetic alone. Detailed per-edge timing adds overhead.

| Preferences | Cost evaluations over all attempts | Exclusive cost | Shape integration | Geometry clipping | Traversal excluding child phases |
| --- | ---: | ---: | ---: | ---: | ---: |
| Off | 56,282 | 9.05 ms | 3.35 ms | 1.61 ms | 3.96 ms |
| Traffic high | 150,126 | 26.61 ms | 8.99 ms | 4.24 ms | 10.97 ms |
| All high | 208,549 | 36.65 ms | 12.52 ms | 5.91 ms | 15.20 ms |

[`evaluate_cost`](../../../rust/src/routing_cost.rs) invokes
[`environment_cost::integrate`](../../../rust/src/environment_cost.rs) for
every evaluated edge when intervals are attached. Integration scans the entire
segment's interval list, clips overlap, and evaluates support for all four
fields. It has no environment aggregate/prefix cache and runs even with all
weights off, computing unknown-length diagnostics. Reverse traversals and
separate leg/assignment searches repeat this work. Shape integrals have a
per-search cache, but each new shortest-path search starts with an empty one.

With shape enabled, each window performs four shortest-path searches. The
traffic-high third window therefore increases the total from eight to twelve.
Increased search work also repeats shape integration and geometry decoding;
the slowdown cannot be attributed entirely to environment arithmetic.

## Follow-up priorities

1. Address repeated loading across nested planning windows: evaluate containing
   graph reuse or remembering a sufficient starting window, while preserving
   boundary/optimality semantics. A larger cache is a confirmed local mitigation.
2. Reduce environment preparation overhead: avoid the SQLite → Python records →
   tuples → Rust conversion chain, and consider reusable packed interval data.
3. Precompute per-segment environment integrals, with prefix sums for clipped
   spans. Keep zero-weight search evaluation cheap while retaining final-route
   unknown-length diagnostics.
4. Investigate stronger admissible lower bounds and frontier expansion policy.
   Increasing heuristic strength arbitrarily would change correctness guarantees.

These are investigation recommendations, not implemented optimizations or
accepted performance targets.

## Local artifacts and reproduction

Ignored local artifacts are in
[`artifacts/environment-profile-2026-11-09`](../../../artifacts/environment-profile-2026-11-09):
`profile_environment.py`, `baseline.json`, `cache256.json`, `instrumented.json`,
and cProfile captures. Only the completed short-route workloads are reported;
an exploratory longer-route run was stopped and is excluded.

```sh
uv run python artifacts/environment-profile-2026-11-09/profile_environment.py \
  --short-only --report artifacts/environment-profile-2026-11-09/baseline.json
uv run python artifacts/environment-profile-2026-11-09/profile_environment.py \
  --short-only --cache-mib 256 --only off traffic_high all_high \
  --report artifacts/environment-profile-2026-11-09/cache256.json
source /Users/anttimc/.cargo/env
uv run maturin build --release --bindings pyo3 \
  --features python,profiling,pyo3/extension-module \
  --out /private/tmp/runmap-environment-profile
```

For native timing, extract the built wheel into a temporary directory and pass
the extracted `runmap/_routing_native.abi3.so` using the script's `--native`
argument, with `--cache-mib 256 --repeats 5`. This loads the profiling extension
in that process without replacing the installed extension or running server.

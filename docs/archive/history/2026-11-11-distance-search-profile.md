# Leppävaara–Oulunkylä distance-only search profile — 2026-11-11

This investigation found that repeated graph preparation, especially environment
attachment, dominates the complete request on this corridor. The distance-only
search itself takes milliseconds. No routing behavior, server configuration,
installed extension, or prepared dataset was changed.

## Inputs and scope

Source: `6556209be45c9a27ccb79c81ec4209f080c8cd7b` plus the existing
profiling-layout corrections in `rust/src/bindings.rs` and
`rust/src/chunk_search.rs`. Those corrections expose `EdgeRef` within the crate
and report its size; they do not change search calculations. Native release
builds used normal Cargo settings without additional CPU/LTO flags. The isolated
normal extension has the same SHA-256 as the installed extension.

Network dataset:
`2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`.
The optional environment control uses the existing
`helsinki-capital-area/environment-join.sqlite`, validated against
`data/environment/prepared/helsinki-capital-area-v3`. Existing signals are loaded
in request-level benchmarks, with their preference Off.

Endpoints near the stations were `(24.8138, 60.2198)` in Leppävaara and
`(24.9675, 60.2294)` in Oulunkylä. Snaps were approximately 19 m from each pin,
on `w1042663570p0` and `w24450859p1`. The route is **9,805.09 m each way**,
19,610.19 m round trip. All preferences are Off, `avoidance_ratio=0`, surface
is All, and stair avoidance remains enabled. Each leg has 319 traversals.

The initial window settled without retries or frontier contact. Its native
graph contains 154,651 active nodes and 199,421 selected segments in 1,077
payload chunks. The API's conservative spatial segment count is 215,358.
Payloads occupy 96,969,024 bytes. Dataset distance scale is 0.987546674;
the normal overlay safety adjustment remains enabled.

Measurements ran on macOS ARM64, CPython 3.14.6. OS caches and CPU frequency
were uncontrolled. Complete requests include JSON serialization, but exclude
HTTP transport, browser rendering, and exact-response caching. Five unprofiled
repetitions follow the first request in each configuration. Profiled requests
are separate from the timing samples. These are local observations, not
production percentiles or cold-disk measurements.

## Complete request: preparation dominates

| Environment data attached; preferences always Off | Graph cache | First request | Repeated median | Median preparation | Median planning |
| --- | ---: | ---: | ---: | ---: | ---: |
| No | 128 MiB | 766 ms | 281 ms | 254 ms | 9.10 ms |
| No | 512 MiB | 695 ms | 26.4 ms | 0.085 ms | 9.03 ms |
| Yes | 128 MiB | 4,454 ms | 3,525 ms | 3,258 ms | 9.31 ms |
| Yes | 512 MiB | 4,105 ms | 25.7 ms | 0.083 ms | 8.78 ms |

The graph's cache accounting is 188,258,368 bytes (179.54 MiB) without
environment data and 269,023,008 bytes (256.56 MiB) with it, including signals.
Both exceed the default 128 MiB allowance. `NumericRoutingData._prepare` returns
an oversized graph without caching it, so every subsequent request loads it
again. The joined intervals add 80,764,640 bytes (77.02 MiB) to the accounting.
The accounting is an allowance, not measured live RSS.

At 512 MiB, this single graph remains cached. Request fingerprints match across
cache sizes within each environment configuration. Routes, costs, and expansion
counts also agree across the joined and unjoined configurations; full response
hashes differ because capabilities and environment diagnostics differ.

The joined/default-cache cProfile capture attributes about 3.96 s to
`_attach_environment`, including 3.43 s in `read_environment_segments` and
0.47 s in native interval conversion. It records roughly 7.1 million Python
calls. These instrumented times diagnose loading and conversion, rather than
replace the unprofiled latency estimates. Interval attachment happens even when
every environment preference is Off. Final-route unknown-length diagnostics
still require environment information under the existing result contract.

This extends the [November 9 cache investigation](2026-11-09-environment-routing-profile.md):
here a single initial graph exceeds the allowance, even without penalties or
window expansion. The 512 MiB experiment establishes a local warm-request
mitigation, not a deployment memory recommendation or a cold-request fix.

## Search regression comparison

The comparison explicitly loads the saved November 10 normal native library
from revision `1420cddbd5ab0c09975f0c584bee7df15f5762f8` and matching Python
source from that revision. Its library hash matches the
[retained prior profile](2026-11-10-native-routing-profile.json).
Both versions search the same fixed graph without environment or signal data,
with matching stair/access profiles and snapped endpoint geometry.

Each version has three rounds of 100 measured searches per direction, with one
warm-up per direction per process. Execution order was current/previous,
current/previous, previous/current. Preparation and result hashing are outside
the search samples. Graphs are immutable and search state is recreated.

| Direction | Previous pooled median | Current pooled median | Expanded nodes in both |
| --- | ---: | ---: | ---: |
| Leppävaara → Oulunkylä | 3.580 ms | 3.670 ms | 24,951 |
| Oulunkylä → Leppävaara | 2.980 ms | 3.069 ms | 21,497 |

Costs, traversed segment/span fingerprints, path-edge counts, and expansion
counts match exactly across every measured round and both revisions. The
pooled timing difference is approximately 2.5–3.0%, with one visibly slower
outbound round in the old build. This does not demonstrate a large search
regression or isolate the cause of a small timing change. The native search
without signal penalties still uses the original node-label algorithm.

## Native search profile

A separate current extension with the `profiling` feature captured ten samples
per direction in coarse, detailed, and allocation-only modes. Timers and
allocation counting are separated. Coarse traversal includes edge costs;
detailed traversal excludes its separately timed cost work.

| Exclusive coarse phase | Outbound median | Return median |
| --- | ---: | ---: |
| Chunk views | 0.071 ms | 0.080 ms |
| Endpoint overlay | 0.002 ms | 0.002 ms |
| Search initialization | 0.059 ms | 0.054 ms |
| Traversal, including edge costs | 3.649 ms | 2.958 ms |
| Result construction | 0.020 ms | 0.019 ms |
| Native serialization | 0.044 ms | 0.043 ms |

Coarse whole-call medians are 3.863 and 3.167 ms; traversal accounts for about
94%. Independent phase medians need not sum to the whole-call median.
Detailed cost evaluation has 35,030 outbound calls and 30,032 return calls,
with measured exclusive totals 0.632 and 0.543 ms. These per-edge timers add
overhead, so they should not be treated as normal-build latency estimates.
Shape integration and hill decoding have zero calls. Endpoint geometry decoding
has two calls per direction. Initialization requests 5,809,760 bytes in three
allocations per search but takes only about 0.06 ms; allocation volume is not
the dominant CPU cost here.

## Follow-up and reproduction

Prioritize avoiding repeated working-set preparation and unnecessary full-area
environment attachment for zero-weight searches, while retaining selected-route
diagnostics. Stronger A* bounds might reduce the few milliseconds spent in
traversal, but would not address the seconds spent loading this request.
No optimization was implemented in this investigation.

The [machine-readable summary](2026-11-11-distance-search-profile.json) retains
round medians, identities, route fingerprints, cache accounting, exclusive
phase timings, and library hashes. Local raw captures, `.prof` files, text
profiles, isolated libraries, and the reproduction harness are in
[`artifacts/distance-profile-2026-11-11`](../../../artifacts/distance-profile-2026-11-11).
The [harness](../../../artifacts/distance-profile-2026-11-11/benchmark.py) accepts
`--native`, `--source-root`, `--requests`, `--environment`, `--cache-mib`,
`--repeats`, and `--report`. It does not install extensions or change the server.

Basic distance-only request reproduction:

```sh
uv run python -m runmap.experiments.benchmark_run_latency \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --home 24.8138 60.2198 --turnaround 24.9675 60.2294 \
  --avoidance-ratio 0 --repeats 5 --timeout-s 60 \
  --report artifacts/distance-profile-2026-11-11/reproduced-request.json
```

Joined graph control and native-only capture, using the local isolated library:

```sh
uv run python artifacts/distance-profile-2026-11-11/benchmark.py \
  --native artifacts/distance-profile-2026-11-11/current.so \
  --requests --environment --cache-mib 512 --repeats 5 \
  --report artifacts/distance-profile-2026-11-11/reproduced-joined.json
uv run python artifacts/distance-profile-2026-11-11/benchmark.py \
  --native artifacts/distance-profile-2026-11-11/current.so --repeats 100 \
  --report artifacts/distance-profile-2026-11-11/reproduced-native.json
```

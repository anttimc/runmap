# Native routing allocation and phase profile — 2026-11-10

Rerun of the [November 2 procedure](2026-11-02-native-routing-profile.md)
after the [native search hot-loop changes](2026-11-10-native-search-hot-loop.md).
The original captures and report are unchanged. These are native workloads on a
fixed loaded graph; they exclude HTTP, window expansion, route rendering, and
response caching. Request-level acceptance remains in the
[current latency scope](../../100ms-routing-architecture.md).

## Source, data, and artifacts

Source was committed revision `1420cddbd5ab0c09975f0c584bee7df15f5762f8`
plus the [profiling corrections](2026-11-10-native-routing-profile.patch).
An isolated snapshot excluded unfinished traffic-light changes in the shared
working tree, which failed to build when this run started. No extension was
installed or replaced; each capture process explicitly loaded its own library.

The corrections report actual compact predecessor slots rather than the obsolete
`Option<Edge>` size, include environment diagnostic arrays in output byte totals,
and remove stale recommendations from the generated HTML. They do not alter
routing calculations.

The Helsinki network dataset was
`2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`.
The working area matches November 2: 34,653 active nodes, 44,930 selected
segments, 230 chunks, and 21,711,472 bytes of numeric payloads. Home was
`(24.795, 60.216)` and turnaround `(24.815, 60.225)`. Defaults and workload
settings match the original procedure. The original capture loaded no environment
join and had all environment preferences off. The additional environment capture
is described below.

Measurements used macOS arm64, Python 3.14, and normal optimized Maturin builds
without additional target-CPU or LTO flags. Exact platform and Python versions
are in the [retained machine-readable summary](2026-11-10-native-routing-profile.json).

Local generated artifacts:

- [Standalone HTML report](../../../artifacts/native-profile-2026-11-10/index.html)
- [PNG overview](../../../artifacts/native-profile-2026-11-10/overview.png) and
  [SVG overview](../../../artifacts/native-profile-2026-11-10/overview.svg)
- [Normal capture](../../../artifacts/native-profile-2026-11-10/baseline.json),
  [instrumented capture](../../../artifacts/native-profile-2026-11-10/instrumented.json),
  and [exclusive phase CSV](../../../artifacts/native-profile-2026-11-10/phases.csv)

Generated artifacts and native binaries are ignored by Git. The summary retains
capture/library hashes, fingerprints, phase medians, and allocation totals.

## Normal timings and allocation traffic

Normal release captures used 40 repetitions after one warm-up per workload.
Each profiling mode used 20 repetitions in a separate process from the normal
capture. Every result fingerprint matched across all repetitions, builds, and
profiling modes. Expanded-node totals, path-edge counts, and completion outcomes
match the November 2 capture. Historical result fingerprints are not asserted
equal: the result schema now includes environment diagnostics.

| Workload | Nov 2 median | New median | New p95 | Median change | Rust allocation requests | Requested MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Explore 1 km | 0.233 ms | 0.203 ms | 0.280 ms | -12.8% | 44 | 0.679 |
| Explore 3 km | 2.467 ms | 1.797 ms | 1.964 ms | -27.1% | 51 | 1.808 |
| Distance-only round trip | 0.402 ms | 0.366 ms | 0.439 ms | -9.0% | 206 | 3.103 |
| Shape round trip | 5.642 ms | 3.731 ms | 3.867 ms | -33.9% | 15,950 | 9.572 |
| Shape + hills-high round trip | 10.022 ms | 6.372 ms | 6.604 ms | -36.4% | 27,006 | 11.915 |

The timing changes compare separate dated runs, with uncontrolled machine and
cache state and intervening code changes. They do not isolate a causal speedup
from one optimization. The hot-loop report provides the separate incremental
comparisons. Shape workloads perform four searches; distance-only performs two,
and preferences change the explored nodes.

## What changed in the allocation profile

`Edge` remains 144 bytes, while `Option<EdgeRef>` is **24 bytes**. Four shape
searches request **4.910 MiB** during initialization, down from 23.323 MiB in
the old profile (78.9% less). This is 51.3% of current shape allocation traffic
and 41.2% with hills high. Coarse initialization medians are 0.047 ms and
0.052 ms respectively. Predecessor storage remains a substantial allocation,
but these timings do not identify it as the main CPU bottleneck.

Shape geometry decoding/clipping now makes **15,500 allocation requests** and
requests **0.686 MiB**, versus 33,041 requests and 1.92 MiB previously. Hills
high makes **26,541 requests** and requests **1.176 MiB**, versus 56,642 and
3.31 MiB. Geometry still accounts for about 97–98% of scoped allocation
requests. The early relaxation rejection and full-geometry fast path reduce
work, but this dated comparison does not separate their contributions.

Detailed shape timings attribute 0.939 ms to decoding/clipping, 1.156 ms to
integration, 2.033 ms to exclusive cost/cache work, and 1.559 ms to exclusive
traversal. Hills-high equivalents are 1.641, 1.994, 3.623, and 2.901 ms.
These are medians of exclusive instrumented phases; independently computed
medians need not sum to the complete workload median. Per-edge timers materially
perturb this hot loop. Geometry, integration, cost/cache work, and traversal
remain candidates for further controlled measurements.

Normal/coarse/detailed workload medians are 3.731/4.133/6.030 ms for shape and
6.372/7.424/10.552 ms for hills high. Use normal-build timings for latency;
detailed instrumentation is a diagnostic breakdown.

Overlay setup remains small: approximately 0.006–0.007 ms in coarse shape
captures. Serialization is approximately 0.032–0.040 ms. Current output array
payloads are 75,264 bytes for shape and 83,520 bytes with hills high. These now
include environment penalty and unknown-distance arrays, so they are not the
same accounting scope as the old output-byte column.

One normal graph preparation took 147.36 ms: 137.22 ms loading payloads and
10.03 ms native validation/indexing. The separate instrumented preparation
requested 15.87 MiB. Normal-process peak RSS was 147.39 MiB. Preparation was
sampled once per process with uncontrolled OS cache; the load-time difference
from the old report does not establish a storage regression. RSS includes
Python, imported libraries, graph payloads, and earlier high-water marks.

## Environment penalty extension

An additional capture adds **shape + traffic high** and an **environment-off
control** using the same joined graph. The original five workloads still load
their original graph without environment data. This separates active preferences
from the cost of retaining the joined intervals; differences in visited nodes
still prevent interpreting the timing gap as pure per-edge integration overhead.

The source is the same isolated revision and native libraries as above, plus the
[environment profiling extension](2026-11-10-native-routing-profile-environment.patch).
The environment join was
`data/paths/prepared/helsinki-capital-area/environment-join.sqlite`, validated
against raster bundle `data/environment/prepared/helsinki-capital-area-v3`.
Its raster dataset identity is
`de078fed501c0d3b0e9290879479063183a61b848183e5c075ea06cbe5f27cbb`;
network and raster hashes are retained under `environment_extension` in the
machine-readable summary. Traffic is high; other environment fields and hills
are off, and shape uses the default settings.

| Workload | Normal median | Normal p95 | Expanded nodes | Rust allocation requests | Requested MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Shape + environment off | 3.760 ms | 3.900 ms | 11,126 | 15,950 | 9.572 |
| Shape + traffic high | 8.687 ms | 9.569 ms | 21,521 | 29,879 | 12.890 |

Traffic high performs four searches and returns two complete candidates with
424 total path edges. The aggregate traffic penalty across both returned
candidates is **8,337.69 cost metres**; all other environment penalties are zero.
The control's penalties are all zero. Fingerprints match across all 40 normal
repetitions and 20 repetitions of every instrumented mode. The five original
workload fingerprints also match the earlier capture exactly.

Traffic-high coarse and detailed medians are 9.248 ms and 12.745 ms. Detailed
exclusive cost/cache time is 5.784 ms, geometry decoding/clipping 1.764 ms,
shape integration 2.142 ms, and traversal 2.722 ms. Environment interval
integration is included in the **cost/cache** phase; it has no separate timer.
Thus the chart's field-integration phase measures shape-field integration and
must not be read as environment-only time.

The joined graph retains an additional 20,220,720 bytes of environment payloads
(19.28 MiB). Its separately sampled preparation took 1,182.15 ms, outside all
workload timings. It loads both numeric payloads and environment intervals;
this single observation is not a cold-disk benchmark or an isolated environment
loading measurement. The process retains both graphs, so its peak RSS is not
directly comparable to the original single-graph capture.

- [Extended visual report](../../../artifacts/native-profile-2026-11-10-environment/index.html)
- [Extended normal capture](../../../artifacts/native-profile-2026-11-10-environment/baseline.json)
  and [instrumented capture](../../../artifacts/native-profile-2026-11-10-environment/instrumented.json)
- [Extended phase CSV](../../../artifacts/native-profile-2026-11-10-environment/phases.csv)

Add these arguments to **both** capture commands and write to the separate
`artifacts/native-profile-2026-11-10-environment` directory:

```sh
--environment data/paths/prepared/helsinki-capital-area/environment-join.sqlite \
--environment-raster-bundle data/environment/prepared/helsinki-capital-area-v3
```

Render the extended captures with the updated `plot_native_profile` module;
it also continues to accept the original five-workload captures. Join identity
mismatches between captures are rejected before rendering.

## Method, validation, and reproduction

Exclusive timer nesting, allocator accounting, and exclusions match the original
procedure. Successful allocations and reallocations count their full requested
sizes. These measure allocation traffic rather than live memory or bytes copied;
Python/SQLite allocations and other threads are excluded. Timing and allocation
passes are separate. Graph input payloads are borrowed; Rust serialization
staging buffers and Python-owned output bytes still require separate storage.

Validation on the isolated measured source: 13 Rust tests without profiling,
14 with profiling, Rust formatting checks, and 47 numeric Python tests against
the captured normal library passed. Report generation verifies graph/source
identities and every result fingerprint across normal and profiling captures.

For a clean checkout, apply the linked profiling patch to revision `1420cdd`,
then use the original report's Maturin build/capture commands with
`artifacts/native-profile-2026-11-10` as the output directory. Restore the normal
extension afterward if using `maturin develop`.

This run instead used `/private/tmp/runmap-native-profile-2026-11-10` as an
isolated source snapshot. The local `artifacts/native-profile-2026-11-10/capture.py`
loads an explicit native library before running the snapshot's standard
`profile_native` CLI. From the repository root:

```sh
source "$HOME/.cargo/env"
uv run --no-sync maturin build --release --locked --bindings pyo3 \
  --features python,pyo3/extension-module \
  --manifest-path /private/tmp/runmap-native-profile-2026-11-10/rust/Cargo.toml \
  --out artifacts/native-profile-2026-11-10/normal-wheel
cp /private/tmp/runmap-native-profile-2026-11-10/rust/target/release/lib_routing_native.dylib \
  artifacts/native-profile-2026-11-10/normal.so
uv run --no-sync python artifacts/native-profile-2026-11-10/capture.py \
  artifacts/native-profile-2026-11-10/normal.so \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  data/paths/prepared/helsinki-capital-area/numeric-routing-v2.sqlite \
  --repeats 40 --report artifacts/native-profile-2026-11-10/baseline.json
```

Repeat the build with `python,pyo3/extension-module,profiling`, save the resulting
library as `profiling.so`, and capture with `--instrumented --repeats 20` to
`instrumented.json`. Generate the visual report:

```sh
uv run --no-sync python -m runmap.experiments.plot_native_profile \
  artifacts/native-profile-2026-11-10/baseline.json \
  artifacts/native-profile-2026-11-10/instrumented.json \
  --output artifacts/native-profile-2026-11-10
```

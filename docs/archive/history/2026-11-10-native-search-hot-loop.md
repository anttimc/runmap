# Native search hot-loop comparison, 2026-11-10

The retained changes reduce fixed-window planning time by 30–51% on the local
Helsinki workload. This measures planning and native distance primitives after
graph preparation; it does not establish a complete API latency improvement.
The [machine-readable results](2026-11-10-native-search-hot-loop.json) preserve
round medians, output comparisons, source/library hashes, and input identities.

## Inputs and measurement

- Platform: macOS ARM64; Rust `1.99.1 (58cf8f9c3 2026-10-13)`, CPython 3.14.
- Native builds: Maturin release, Python extension enabled, profiling disabled;
  default Cargo release settings, without additional LTO or target-CPU flags.
- Network dataset:
  `2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`.
- Prepared feature identity:
  `54d3377ce4c981966caa15f603987010c8b570ab6ff6365517c71fbbee9df3f8`.
- Environment dataset:
  `de078fed501c0d3b0e9290879479063183a61b848183e5c075ea06cbe5f27cbb`;
  four mapped fields, `helsinki-capital-area-v3`.
- Endpoints: `(24.795, 60.216)` to `(24.815, 60.225)`.
- Fixed EPSG:3067 bounds:
  `(374971.56657009746, 6675095.112818664, 381827.50090309826, 6680883.1672423985)`.
- Default planner settings and surface profile. Distance disables shape with
  `avoidance_ratio=0`; other planning cases use default shape. Combined enables
  hills and all four environment preferences at High. Traffic High is weight 4.
- Exploration uses a 3,000 m budget in both directions from the same start.

Each build runs in a fresh Python process that explicitly loads its preserved
native library. Graph preparation and one warm-up per workload are excluded.
Result fingerprints are computed outside timing and include all result fields,
arrays, diagnostics, and expansion counts. There is no route-result cache.
The operating-system cache and CPU frequency were uncontrolled.

The initial matrix used three deterministically shuffled rounds of 60 measured
samples per workload per build. A second matrix compared calling conventions.
The final baseline/candidate comparison used three interleaved rounds of 100
samples with the maintained benchmark module. Tables report pooled medians;
individual round medians are in the JSON. Separate matrices should not be used
to attribute small timing differences to a single change.

## Incremental experiments

Planning medians in milliseconds from the initial matrix:

| Build | Distance | Shape | Hills High | Traffic High | Combined |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 0.733 | 6.631 | 11.907 | 12.727 | 23.201 |
| 01: early rejection | 0.586 | 5.521 | 9.691 | 10.221 | 18.597 |
| 02: scalar search cost | 0.401 | 4.540 | 7.716 | 10.220 | 18.286 |
| 03: full-geometry fast path | 0.399 | 4.135 | 7.064 | 9.289 | 16.771 |
| 04: environment hash cache, rejected | 0.402 | 3.872 | 6.561 | 10.225 | 18.432 |
| 05: borrow throughout, cache removed | 0.399 | 5.049 | 8.551 | 11.215 | 19.911 |
| 06: compact predecessors | 0.346 | 4.024 | 6.818 | 9.187 | 16.114 |

Steps 01–03 are cumulative. Step 04 adds a search-local cache keyed by physical
segment/span, reusing weighted environment penalties across directions. It
slowed Traffic and Combined by about 10%; it was removed before step 05.
Its zero-weight timing changes illustrate the sensitivity to code and state
layout, rather than a benefit from caching inactive environment terms.

Step 05 changes visits and downstream cost functions to references. Shape,
Hills, Traffic, and Combined became roughly 20–22% slower than step 03. Fewer
source-level copies did not establish a performance benefit.

Step 06 retains references and replaces per-node `Option<Edge>` values with
base-arc or overlay-edge identifiers. Optimized LLVM output shows predecessor
slots shrinking from 144 to 24 bytes on this platform. Full edges are resolved
only during path reconstruction. Distance exploration does not allocate
predecessors.

The calling-convention matrix then compared step 06 with step 07: borrowed
visits and compact predecessors, but by-value downstream cost helpers. Step 07
reduced shape/hill/traffic/combined planning time by about 5–6% relative to step
06, while exploration became about 2% slower. This hybrid is retained.

## Retained implementation and final comparison

1. Reject an edge before evaluating costs when the current accumulated cost is
   already at least its neighbor's best label. All objective terms are
   nonnegative; the existing strict-improvement rule and queue ordering remain.
2. Evaluate a scalar search objective, integrating one hill feature instead of
   all seven. Share hill integration, shape costs, environment arithmetic, and
   component validation with detailed evaluation. Skip inactive environment
   fields and zero-weight environment evaluation during search; selected paths
   still report all unknown-length diagnostics. Return the validated total
   without summing its components again in relaxation.
3. For geometry without clipping bounds or endpoint overrides, use decoded
   points directly and remove consecutive duplicates. Partial overlay edges
   retain clipping and endpoint overrides. No sampling rule changes.
4. Borrow edges during visits and keep compact predecessor identifiers. Pass
   values into downstream cost helpers because that calling convention
   performed better locally.

Final medians, 300 measured samples per workload/build:

| Workload | Baseline, ms | Retained, ms | Reduction |
| --- | ---: | ---: | ---: |
| Distance planning | 0.731 | 0.359 | 50.8% |
| Shape planning | 6.291 | 3.786 | 39.8% |
| Hills High | 11.085 | 6.436 | 41.9% |
| Traffic High | 11.965 | 8.384 | 29.9% |
| Combined | 21.721 | 15.250 | 29.8% |
| Forward exploration | 1.867 | 1.800 | 3.6% |
| Reverse exploration | 1.871 | 1.814 | 3.1% |

Every measured build and round matched baseline fingerprints exactly on all
seven workloads. Expanded planning nodes were respectively 2,479, 11,126,
19,172, 21,521, and 36,240. Both exploration cases reached 17,469 nodes.
These counts aggregate the planner's returned legs, not all rejected candidates
or API window attempts.

Early rejection intentionally omits evaluation of edges that cannot improve a
label. Inactive environment diagnostics are deferred to selected paths. Prepared
input validation remains at the native graph/environment boundary; these
optimizations do not promise to detect malformed unused costs by traversing
every irrelevant edge. Full-geometry interpolation roundoff can differ on other
geometries even though the benchmark fingerprints matched exactly.

Validation includes 13 normal Rust tests, 14 with profiling enabled, 47 Python
numeric/native/reference tests, formatting, and Clippy with and without Python
bindings. New checks compare scalar and detailed objectives across directions,
full/partial chainage, shape/reuse, active/inactive environment fields, missing
support, duplicate geometry points, and endpoint overrides. These checks do not
replace local runner acceptance or wider saved-request calibration.

## Reproduction and limits

The maintained runner is
[`benchmark_search_hot_loop.py`](../../../src/runmap/experiments/benchmark_search_hot_loop.py).
Build and preserve each library before changing sources:

```sh
uv run --no-sync maturin build --release --locked --out artifacts/search-wheels
cp rust/target/release/lib_routing_native.dylib artifacts/search-candidate.so
uv run --no-sync python -m runmap.experiments.benchmark_search_hot_loop \
  --native artifacts/search-candidate.so --repeats 100 \
  --report artifacts/search-candidate.json
```

Repeat in separate baseline/candidate processes, alternating order across three
rounds. Compare every workload's `hashes` and `size` before comparing timing.
Keep instrumented builds separate. No prepared artifact regeneration is needed.

Ignored local artifacts under `artifacts/hot-loop-2026-11-10` retain the initial
script, interleaving driver, all raw samples, source snapshots, and native
libraries for builds 00–07. `summary.json`, `convention-summary.json`, and
`final-summary.json` correspond to the three matrices above.

The rejected hash cache does not evaluate graph-owned precomputed environment
aggregates or prefix sums; those remain separate possible improvements. Cold
loading, complete API requests, larger working areas, other CPUs/toolchains,
and broader saved-route acceptance remain unmeasured by this comparison.

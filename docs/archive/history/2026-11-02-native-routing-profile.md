# Native routing allocation and phase profile — 2026-11-02

This is a dated diagnostic of the Rust library, not an end-to-end API benchmark
or an optimization result. No routing algorithm or cost semantics were changed.
See [current latency scope](../../100ms-routing-architecture.md) for request-level
measurements and outstanding acceptance.

## Artifacts and scope

- [Standalone visual report](../../../artifacts/native-profile-2026-11-02/index.html)
- [PNG overview](../../../artifacts/native-profile-2026-11-02/overview.png) and
  [SVG overview](../../../artifacts/native-profile-2026-11-02/overview.svg)
- [Normal release capture](../../../artifacts/native-profile-2026-11-02/baseline.json),
  [instrumented capture](../../../artifacts/native-profile-2026-11-02/instrumented.json),
  and [phase CSV](../../../artifacts/native-profile-2026-11-02/phases.csv)

Generated artifacts live in the ignored `artifacts/` directory and can be
regenerated with the commands below; this dated summary is retained in Git.

Measurements ran on macOS 26.6.2, arm64, Python 3.14.6, using optimized Rust
builds. Source revision was `6ee79f60fdd3acc6d9d121ee7245ce7c9d9bb262`
plus the profiling additions in the working tree. The Helsinki dataset identity
was `2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`.

The fixed working area contained 34,653 active nodes, 44,930 selected segments,
230 chunks, and 20.7 MiB of numeric payloads. Home was `(24.795, 60.216)` and
turnaround `(24.815, 60.225)`. Requests used default shape settings and the
wrappers' default profile (all surfaces, stairs allowed). The graph remained
loaded; each repetition recomputed search state and costs. No automatic area
expansion, HTTP, route rendering, or exact-response cache was included.

## Observations

Normal release timings used 40 repetitions after a warm-up per workload.
Profiling used 20 repetitions per workload and mode, in a separate process.
Every result fingerprint matched across builds, repetitions, and profiling modes.

| Workload | Normal median | Normal p95 | Expanded nodes, total | Rust allocation requests | Cumulative requested MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Explore 1 km | 0.233 ms | 0.334 ms | 2,374 | 44 | 0.679 |
| Explore 3 km | 2.467 ms | 3.191 ms | 17,469 | 51 | 1.808 |
| Distance-only round trip | 0.402 ms | 0.542 ms | 2,479 | 166 | 12.270 |
| Shape round trip | 5.642 ms | 5.994 ms | 11,126 | 33,411 | 29.135 |
| Shape + hills-high round trip | 10.022 ms | 10.690 ms | 19,172 | 57,027 | 32.369 |

Shape workloads perform four shortest-path searches (two assignments, two legs).
Distance-only performs two. They also visit different nodes, so these totals
do not isolate the incremental cost of one preference.

### Search state dominates allocated bytes, not elapsed time

`Edge` and `Option<Edge>` both occupy **144 bytes** on this build. Each path
search initializes a predecessor array and best-cost array for every indexed
node, including nodes outside the active bounds retained in the loaded chunks.
Four searches requested 23.323 MiB during initialization: 80.1% of all scoped
Rust allocation bytes with shape enabled, and 72.1% with hills high.

Coarse initialization timings were about 0.192 ms for shape and 0.331 ms for
shape plus hills, compared with complete workload medians of 5.901 ms and
10.799 ms under coarse instrumentation. Thus this is a clear memory-traffic
target, but those measurements do not establish it as the main CPU bottleneck.
Compact predecessor references and reusable request-owned buffers are candidates.

### Geometry dominates allocation count

Geometry decoding and clipping issued **33,041** allocation/reallocation
requests for the shape workload and **56,642** with hills high. These represented
about 99% of scoped allocation requests, but requested only 1.92 MiB and
3.31 MiB respectively. Geometry vectors are allocated on shape-cache misses;
the cache is rebuilt for each shortest-path call.

Detailed instrumentation attributed approximately 1.26 ms to geometry decoding
plus clipping and 1.46 ms to integration for shape, increasing to 2.23 ms and
2.52 ms with hills high. These are instrumented diagnostic times, not precise
production shares. Avoiding intermediate geometry vectors and repeated geometry
passes is a plausible CPU optimization; it still requires a before/after benchmark.

The coarse traversal phase, including cost evaluation, occupied about 92–94%
of the shape workloads' total time. Detailed timers further split that phase
into traversal, costs/cache work, geometry, and integration.

### Endpoint scans and output copies are smaller here

Coarse overlay setup took approximately 0.009 ms per shape workload. Rust
serialization staging and Python bytes creation took about 0.034–0.042 ms.
Those paths are not the first CPU targets in this fixed working area.

Shape results contained 50,176 bytes of output payloads; hills-high results
contained 55,680. Rust first serializes columns into temporary vectors, then
copies them into Python-owned bytes. The counters see the Rust staging buffers
and their growth, but do not count Python's allocation or directly measure copy
traffic. NumPy subsequently uses buffer views. Graph input payloads are borrowed;
per-call chunk-view construction still allocates descriptor vectors.

One normal-build graph preparation took 37.50 ms, including 10.22 ms for native
validation/index construction and 27.15 ms for payload loading. The separate
allocation pass counted about 15.67 MiB requested during index construction.
These are single observations with uncontrolled OS cache and must not be treated
as cold-storage timings. Normal-process peak RSS was about 153 MiB and includes
Python, imported libraries, retained graph data, and earlier workload high-water
marks; it is not a per-phase allocation measurement.

## Measurement method and limitations

The optional Cargo `profiling` feature enables thread-local RAII phase spans,
Python session controls, and a wrapper around Rust's `System` allocator. Normal
builds compile span calls to no-ops and expose no profiling controls.

Nested times and allocation counters are subtracted from their parents, producing
exclusive phase measurements. Coarse timers cover phase boundaries; detailed
timers also run per edge and per shape-cache miss. Allocations are recorded in a
separate pass without timers. Successful allocations and reallocations are counted;
each realloc contributes its full requested new size. Counts therefore describe
allocation traffic, not live bytes, peak memory, or bytes physically copied.
Python/SQLite allocation requests and other threads are excluded. Allocations
outside the instrumented spans are not attributed to a phase.

Detailed timing increased shape workload medians from 5.642 ms to 7.880 ms and
10.022 ms to 13.867 ms. Coarse medians were 5.901 ms and 10.799 ms. This visible
overhead is why detailed percentages are diagnostic, and production timings come
from the separate normal build. Sequential runs also include machine noise and
uncontrolled cache effects. No causal speedup can be inferred without changing
one implementation choice and repeating the benchmark.

Validation: Rust tests passed with and without profiling; the allocation test
checks exclusive nested accounting and session reset. Native search, API planning,
and exploration tests were run after restoring the normal extension. Raw capture
fingerprints include all returned result fields, costs, arrays, and node counts.

## Reproduction

Run from the repository root with the prepared Helsinki artifacts present and
the web and experiments extras installed. Explicit binding features are included
because command-line `--features` overrides Maturin's configured features.

```sh
source "$HOME/.cargo/env"
uv run --no-sync maturin develop --release --locked --uv \
  --bindings pyo3 --features python,pyo3/extension-module
uv run --no-sync python -m runmap.experiments.profile_native \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  data/paths/prepared/helsinki-capital-area/numeric-routing-v2.sqlite \
  --repeats 40 --report artifacts/native-profile-2026-11-02/baseline.json

uv run --no-sync maturin develop --release --locked --uv \
  --bindings pyo3 --features python,pyo3/extension-module,profiling
uv run --no-sync python -m runmap.experiments.profile_native \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  data/paths/prepared/helsinki-capital-area/numeric-routing-v2.sqlite \
  --instrumented --repeats 20 \
  --report artifacts/native-profile-2026-11-02/instrumented.json

uv run --no-sync python -m runmap.experiments.plot_native_profile \
  artifacts/native-profile-2026-11-02/baseline.json \
  artifacts/native-profile-2026-11-02/instrumented.json \
  --output artifacts/native-profile-2026-11-02

# Restore the normal extension after profiling; restart any running API worker.
uv run --no-sync maturin develop --release --locked --uv \
  --bindings pyo3 --features python,pyo3/extension-module
```

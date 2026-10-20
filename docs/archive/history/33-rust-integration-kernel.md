# First Rust integration kernel

Measured 2026-10-20. This report describes the first scalar native implementation;
current setup and outstanding validation belong in [development](../../development.md)
and the [roadmap](../../roadmap.md#next-rust-integration-kernel-and-interactive-routing).

## Implementation

The first increment moves complete per-geometry midpoint integration to Rust.
The Rust compiler builds the numerical code; Cargo manages Rust dependencies;
PyO3 exposes the function to Python; Maturin builds the mixed package through
the Python build interface used by uv. The Python API and lazy request cache
remain in place, with one native call per physical geometry. There is no spatial
batching, multithreading, SIMD implementation, or native search in this increment.

The binding converts Python coordinate pairs to a Rust vector, then borrows a
slice for the numerical function. Plain settings/frame records hold data;
functions perform validation, frame preparation, sample integration, and timeout
checks. Rust errors become Python `ValueError` or `TimeoutError`. The Python
wrapper checks absolute deadlines around the call; Rust checks a remaining
duration using `Instant`, without assuming the clocks have a shared epoch.
The binding keeps the interpreter lock for the first version.

The previous Python/NumPy integrator remains available as
`integrate_fields_python`. Missing native support raises an import error rather
than selecting a hidden fallback. Native results were compared to scalar
midpoints and the Python implementation with relative tolerance 1e-12 and
absolute tolerance 1e-9 for accumulated integrals, allowing ordinary floating
point differences between math libraries and reduction order.

## Environment and workload

- macOS 26.6.2, ARM64; CPython 3.14.6.
- Rust 1.99.1, PyO3 0.30.1, Maturin 1.15.0; optimized release build.
- `leppavaara-flat-10k/graph.json`, SHA-256
  `6a8b61db10ffc7650b26ff4757583a6c3ea3c92cd516ef882188265febf295de`.
- Home (24.795, 60.216), turnaround (24.815, 60.225), default `RunSettings`,
  hills off. Both side assignments and their outbound/return legs are calculated.
- 8,053 integrated geometries per request; 20 timed repetitions after warming.
  Each planner request gets a fresh feature cache. Python measurements patch only
  the integrator, leaving the same planner, data, and settings in place.
- Loading, index preparation, snapping, HTTP handling, and browser rendering are
  excluded. The integration benchmark includes Python calls, argument conversion,
  result conversion, the wrapper, and collection of results; it is not a pure
  Rust microbenchmark. p95 uses the nearest-rank value from 20 samples.

## Results

| Work | Python median | Rust median | Python p95 | Rust p95 |
| --- | ---: | ---: | ---: | ---: |
| All request field integrations | 24.15 ms | 2.36 ms | 24.44 ms | 2.40 ms |
| Complete planner request | 75.45 ms | 51.89 ms | 76.42 ms | 53.17 ms |

Integration was approximately 10.2 times faster including conversion overhead;
the complete planner was approximately 31% faster. Both legs' arc sequences
matched for both candidates, with matching costs within the stated tolerance.
The full Python suite passed 200 tests with the native kernel active; four pure
Rust unit tests passed. Differential tests include narrow/wide fields, bends,
reversed/repeated points, empty geometry, invalid inputs, and timeouts during
a very long native calculation. These results do not establish performance for
other routes, supported runtimes, simultaneous requests, or browser interactions.

The declared Rust minimum is 1.85 and the extension targets CPython's stable ABI
from 3.11. Those compatibility floors have not yet been runtime-tested; this
measurement validates only the environment above. The initial integration result
also does not establish an instant response through the full application.

## Reproduction

Follow [native setup](../../development.md#native-integration-kernel), then:

```sh
uv run --no-sync maturin develop --release --locked --uv
uv run --extra web python -m runmap.experiments.benchmark_integration \
  --repeats 20 --output artifacts/pathfinding-profile/first-rust/integration-benchmark.json
```

The retained [JSON report](33-rust-integration-kernel.json) records the initial
timings, graph digest, endpoints, and settings. Re-running creates fresh evidence;
do not overwrite this dated report to imply validation of a later implementation.

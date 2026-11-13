# Compact hill inputs — 2026-11-13

This follow-up fixes the warm hill-edit regression recorded in the
[initial packed-input report](2026-11-13-packed-routing-inputs.md). Native hills
use contiguous arrays addressed by topology chunk/segment, replacing per-segment
allocations and hash lookups. Normal forward/reverse pairs share six feature
values exactly; asymmetric vectors retain all source values. Hill-only attachment
does not build an area-wide segment-length map. Partial endpoint detail remains
request-owned and sparse.

Packed loading discards legacy hill columns before retaining topology. An explicit
conversion produces topology schema/format 3 without hill columns or interval
detail, paired with independently compressed input storage version 2. Hills have
one routing source in that generation. Both shared stores can retain up to half
the existing cache budget; exact-area graphs use remaining accounted capacity.
The default total budget stays 128 MiB, with conservative owner accounting and
active leases independent of eviction.

## Method and artifacts

Measurements use base revision `49c2b5c` plus this change, a normal release native
build with SHA-256
`eab241158c58de582a49cc8972c051600b9ccdecda15ff9a01e10dcb2fc3922a`,
Apple M5/16 GiB, macOS ARM64, CPython 3.14.6, and SQLite 3.53.4. Dataset
`2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`,
feature identity
`54d3377ce4c981966caa15f603987010c8b570ab6ff6365517c71fbbee9df3f8`,
and environment/signal sources match the preceding report.

The converted artifacts contain the same 10,662 channel chunks and 609,933 segment
owners/detail records. Topology occupies 327,987,200 bytes (313 MiB), SHA-256
`b4f654a4aa08d90d0efaed6b8d6b8a94310a429fc0b4bf4413a5f46417fe6b28`.
Compressed inputs occupy 332,836,864 bytes (317 MiB), SHA-256
`71e7d7f3f3e81ebed68a9c13eafb65645d5a4dd23151ecd80c75a9a00fc5b2f5`.
Their paired input identity is
`edd521ac81d0e76bc70cdc7bd76839926b03b66ed6d1096dc340b350439a45ce`.
Source artifacts remain unchanged; conversion publishes a separate directory.

Every configuration uses a fresh worker, one first pass, and three repeated
passes. The [four-edit sequence](2026-11-12-routing-edit-scenarios.json) contains
distance-only, shaped, hills High, and moved-endpoint requests. Candidate lengths
range from 30.43 km to approximately 42 km; some hill candidates exceed the
40 km target. The moved pin is an endpoint, not an implemented intermediate pin.
The [mixed case](2026-11-12-routing-mixed-scenarios.json) enables hills, signals,
greenery, water, and traffic Low, returning two approximately 39.63 km candidates.
Limits remain 650,000 network segments, five retries, 2,500 km², and a cooperative
15-second deadline.

The [machine-readable report](2026-11-13-compact-hill-inputs.json) preserves all five
final configurations, individual timings, phases, cache states, source identities,
memory, and fingerprints. Raw captures and exploratory iterations remain under
[`artifacts/hill-fix-2026-11-13`](../../../artifacts/hill-fix-2026-11-13).

## Results

Repeated medians, milliseconds, at the default 128 MiB budget:

| Request | Original packed build | Same-build legacy sources | Topology 3 + compressed inputs | Existing topology 2 + raw inputs, fixed runtime |
| --- | ---: | ---: | ---: | ---: |
| Distance only | 252 | 220 | 209 | 251 |
| Shaped | 947 | 930 | 904 | 1,016 |
| Hills High | 2,134 | 1,175 | 1,198 | 1,247 |
| Moved endpoint, hills High | 2,200 | 1,173 | 1,154 | 1,223 |

The original column is historical evidence from the preceding build; the other
three columns use the same final native build. Converted hill preparation is
430 ms and planning 607 ms, versus 435/611 ms with legacy sources. All 3,261 needed
hill chunks now fit in the input cache at about 49.1 million accounted bytes.
Repeated hill edits perform no input payload reads or native input decoding;
input-store access takes about 1.7 ms. Topology still reloads and rebuilds area
indexes. The final four-edit peak RSS is 697 MiB, compared with 775 MiB in the
same-build legacy worker and 902 MiB in the original packed report. RSS varies
between workers and includes concurrent batches; it is not the retention budget.

Cold channel activation still costs extra work. The first hills-enabled edit in
the converted sequence takes 1.64 seconds, versus 1.29 seconds with legacy
embedded totals and 2.77 seconds with existing raw packed files on the fixed
runtime. Only the first edit starts with all application caches empty. These are
not cold-storage measurements, and warm medians do not establish first-activation
or process-cold acceptance.

Mixed preferences improve from the original packed 5.32-second repeated median
to 4.37 seconds: preparation is 1.97 seconds and planning 2.11 seconds. First
request latency is 4.87 seconds, and peak RSS is 1,115 MiB. The default cache still
cannot retain the mixed working set. A diagnostic 3 GiB control retains both area
graphs, giving a 2.20-second repeated median, including 2.12 seconds of planning
and 0.3 ms preparation. Its peak RSS is 1,319 MiB; conservatively accounted retained
data is about 1.95 billion bytes. Increasing retention does not solve search time.

Converted startup takes 0.83–1.44 seconds across these workers, versus 6 ms with
legacy sources, and includes packed hill-identity scanning. Startup is excluded
from request timing. OS caches, storage state, CPU frequency, and other process
activity are uncontrolled. Requests include JSON serialization and exclude HTTP,
browser rendering, and debounce. Three samples do not establish production
percentiles or the one-second visible interaction target.

## Correctness and reproduction

All first-pass, repeated, and concurrent responses complete and match the preceding
report's complete response fingerprints, excluding only elapsed time. Three paired
distance/moved-endpoint batches pass in each four-edit configuration, with global
phase instrumentation disabled during concurrency. Tests cover compact symmetric
and exact asymmetric hill vectors, reordered chunk segments, partial/reversed
traversals, all-channel/Off responses, eviction/closure, stale/missing inputs,
compressed corruption/expansion/trailing data, conversion cleanup, and deadlines.
Validation passed 534 Python tests with 675 subtests, 25 Rust tests, Clippy with
warnings denied, and Rust formatting checks.

Use the [current conversion instructions](../../development.md#export-a-sqlite-network-dataset).
The measured generation is under `artifacts/hill-fix-2026-11-13/compressed`, with
links to the unchanged source network and signals for default discovery. Run:

```sh
uv run --no-sync python -m runmap.experiments.benchmark_routing_edits \
  artifacts/hill-fix-2026-11-13/compressed/network.sqlite \
  --scenarios docs/archive/history/2026-11-12-routing-edit-scenarios.json \
  --cache-mib 128 --repeats 3 --concurrency 2 \
  --environment data/paths/prepared/helsinki-capital-area/environment-join.sqlite \
  --environment-raster-bundle data/environment/prepared/helsinki-capital-area-v3 \
  --report artifacts/hill-fix-2026-11-13/final-off-128.json
```

For mixed preferences, select `2026-11-12-routing-mixed-scenarios.json` and omit
`--concurrency`. Use `--cache-mib 3072` only for the cached diagnostic control.
The legacy control directory links the same network, numeric-v2, and signal files
without packed inputs. The raw-input compatibility control uses the original
source directory with its unchanged v1 packed artifact.

Broader locations/coverage, cold activation/storage, sustained concurrency, and
browser acceptance remain in the [roadmap](../../roadmap.md). Area-wide indexing,
serialized preparation, in-flight memory, cancellation, and search remain further
architectural work.

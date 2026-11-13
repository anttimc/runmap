# Packed routing inputs — 2026-11-13

The optional `routing-inputs-v1.sqlite` artifact groups hills, environment fields,
and signals under one validated input store. `NumericEndpoint` now contains only
location data. Hill totals, environment intervals/support, and directed signal
records load by topology chunk; exact partial hill detail loads by segment.
Environment interval vectors and hill totals retain native shared ownership across
overlapping graphs. Legacy artifacts remain readable without conversion.

This delivers storage and ownership boundaries, not the one-second interaction
target. Mixed-preference loading improves substantially, but enabling packed hills
alone currently regresses latency and memory. Keep the artifact optional until
that regression is addressed. Current commands and format contracts belong in
[development](../../development.md#export-a-sqlite-network-dataset) and the
[numeric reference](../../numeric-routing-format.md#packed-routing-inputs).

## Workloads and correctness

The routing base is `4ae2044`, plus this implementation. All configurations use
the same normal release native build, SHA-256
`5fea59f125d8a15acaa65993a6b1485b51958413d935736f2b6ce5bd2320d8e7`,
Apple M5/16 GiB machine, CPython 3.14.6, and SQLite 3.53.4.
Dataset `2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`
and feature identity
`54d3377ce4c981966caa15f603987010c8b570ab6ff6365517c71fbbee9df3f8`
match the [November 12 measurements](2026-11-12-shared-store-measurements.md).
Environment source/raster identities are unchanged.

The packed artifact contains 10,662 channel chunks and 609,933 physical segments
with separate hill detail, occupying 911,515,648 bytes (869 MiB). Its source
identity is `c09fbf8f93a2aee36d6c3d0acf9c592b6821433883c29443b7fa297ff5aa9efa`;
file SHA-256 is `7d23e38c50f574221adad6f2dac9158c560d60229f9e19c6d4a3c3928c4876e6`.
Identity includes the separated numeric hill checksums, so changed partial hill
distributions cannot reuse stale packed data even when segment totals agree.

Each configuration uses a fresh worker, a first ordered pass, and three repeated
passes. The [four-edit sequence](2026-11-12-routing-edit-scenarios.json) keeps
environment/signals Off, with hills High for its last two edits. Its moved pin
is an endpoint edit, not an implemented intermediate dragged-route pin. Some hill
candidates exceed 40 km. The [mixed case](2026-11-12-routing-mixed-scenarios.json)
enables hills, signals, greenery, water, and traffic Low and returns two candidates
of approximately 39.63 km. Limits remain 650,000 network segments, five retries,
2,500 km², and a cooperative 15-second deadline.

Every completed packed response fingerprint matches the earlier complete response
for that request, excluding only elapsed time. The four-edit fingerprints also
match the same-build legacy control exactly. This covers geometry, costs,
traversals, observations, capabilities, and search metadata. Tests cover partial
and reversed hills, environment support/unknown values, external signal arrivals,
changed endpoints with shared graphs, channel changes, corrupt/stale artifacts,
deadline recovery, eviction, closure, and concurrent search. Validation passed
526 Python tests, 660 subtests, 23 Rust tests, Clippy, and formatting checks.

The [machine-readable report](2026-11-13-packed-routing-inputs.json) preserves all
six configurations, individual samples, phases, cache states, fingerprints, source
identities, and memory. Raw captures remain under
[`artifacts/packed-inputs-2026-11-13`](../../../artifacts/packed-inputs-2026-11-13).

## Observations

Repeated medians at the default 128 MiB retention budget:

| Request | Legacy sources, same build | Packed inputs |
| --- | ---: | ---: |
| Distance only, 30.43 km | 222 ms | 252 ms |
| Shaped, 38.54 km | 935 ms | 947 ms |
| Hills High | 1,180 ms | 2,134 ms |
| Moved endpoint, hills High | 1,173 ms | 2,200 ms |
| Mixed preferences, about 39.63 km | 15,490 ms, deadline | 5,323 ms, complete |

The mixed case's first request changes from a 15.45-second deadline to a complete
6.24-second response. Packed repeated preparation takes 2.58 seconds and complete
planning 2.27 seconds. Legacy repeated preparation takes 13.51 seconds, including
12.84 seconds attaching environment records. Its search is interrupted; its partial
search timings are not comparable with complete packed planning. Mixed peak RSS
falls from 2,569 MiB to 1,247 MiB in these same-build default-budget controls.

The hill-only regression is material. Packed hill preparation rises from about
437 ms to 1,228 ms, with 625 ms reading/preparing hill chunks, and planning rises
from 614 ms to 752 ms. Topology format 2 still contains legacy hill totals, while
the packed path additionally loads totals and builds per-segment shared-input
lookups. Partial hill detail remains sparse; this regression does not come from
loading all-area interval detail. The four-edit peak RSS rises from 634 MiB to
902 MiB. Distance-only and shaped requests load no packed cost channels.

The 3 GiB diagnostic control retains both area graphs, all 3,261 topology chunks
needed by those windows, and 9,783 channel chunks. Repeated mixed preparation is
0.30 ms; total latency is 2.36 seconds, including 2.26 seconds of planning. Peak
RSS is 1,314 MiB, while conservative retained-cache accounting charges 2.34 GiB
because owners count in both stores and graphs. An 8 GiB control retains the same
objects and gives the same 2.36-second result; larger cache is not a search fix or
a deployment recommendation. The default cache cannot retain those working sets
and repeats 15,456 input-chunk reads across its two windows per mixed request.

Packed startup is 0.86–0.97 seconds versus 3–6 ms for the legacy controls and
includes scanning hill checksum metadata for identity validation. Startup is
excluded from request timings. Three paired concurrent distance/moved-endpoint
batches also complete; their instrumentation is disabled as in the earlier study.

OS caches, CPU frequency, and storage state are uncontrolled. Legacy controls use
temporary sibling links to the same immutable network/numeric/signal files,
without a packed sibling. No source artifact is replaced. Measurements include
JSON serialization and exclude HTTP, browser rendering, and debounce. Three
repeats do not establish production percentiles or the visible one-second target.

## Reproduction and remaining work

Prepare the optional artifact using the current development instructions. Run:

```sh
uv run --no-sync python -m runmap.experiments.benchmark_routing_edits \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --scenarios docs/archive/history/2026-11-12-routing-mixed-scenarios.json \
  --cache-mib 128 --repeats 3 \
  --environment data/paths/prepared/helsinki-capital-area/environment-join.sqlite \
  --environment-raster-bundle data/environment/prepared/helsinki-capital-area-v3 \
  --report artifacts/packed-inputs-2026-11-13/mixed-128.json
```

For the four edits, use `2026-11-12-routing-edit-scenarios.json` and `--concurrency 2`.
For cache controls, change `--cache-mib` to 3072 or 8192. For legacy controls,
create a separate directory with sibling links to `network.sqlite`,
`numeric-routing-v2.sqlite`, and `signals.sqlite`, pass that network path, and keep
the same environment arguments; omit the packed sibling.

Next remove duplicated legacy hill storage and reduce hill lookup/attachment work
before making packed inputs standard. Area-wide indexes, serialized preparation,
bounded in-flight memory, cancellation, and faster search remain outstanding.
Broader coverage, additional starts, sustained concurrency, and browser acceptance
remain validation tasks in the [roadmap](../../roadmap.md).

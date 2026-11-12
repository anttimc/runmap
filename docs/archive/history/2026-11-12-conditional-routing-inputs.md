# Conditional prepared routing inputs — 2026-11-12

Hills, mapped environment fields, and signals now share one prepared-input
boundary. API search loads environment/signal channels only when enabled; with
environment weights Off, returned-route unknown-length observations are completed
after window selection. Hills retain directional totals and endpoint-only detail
through that same boundary. Existing artifact layouts and cost calculations remain
compatible; packed active-channel storage is still planned.

## Workload and correctness

This follows the [shared-store measurements](2026-11-12-shared-store-measurements.md)
using the same [four ordered edits](2026-11-12-routing-edit-scenarios.json),
128 MiB retention budget, configured environment join, 15-second deadline, and
three repeated passes. Environment and signal preferences are Off; the hill
cases enable hills High. Three two-request batches follow the serial passes.
Hills therefore exercise a separate channel rather than being excluded from the
common input model.

The routing base is `a4e09c3`, plus the implementation accompanying this report.
The native release SHA-256 is
`e794e8023ba8f4e2810a64ec6f15fb17da139b965f283ee7165a8048f1816573`.
Network dataset, feature identity, environment raster, Apple M5/16 GiB machine,
CPython 3.14.6, and SQLite 3.53.4 match the earlier report. No artifact was
re-exported or replaced.

Every first-pass and repeated completed response fingerprint matches the earlier
complete response from the large-cache control for that exact request. These
fingerprints include costs, geometry, traversals, observations, capabilities, and
search metadata, excluding only elapsed time. Synthetic tests also compare eager
and deferred native observations exactly for partial, reversed, and repeated
traversals; channel changes cannot reuse an incompatible graph. Missing returned-
route input detail fails rather than becoming an unknown-length zero.

The [machine-readable summary](2026-11-12-conditional-routing-inputs.json) retains
individual timings, phases, statuses, fingerprints, and cache/memory accounting.
Full local captures are in
[`artifacts/conditional-inputs-2026-11-12`](../../../artifacts/conditional-inputs-2026-11-12).

## Repeated request observations

| Edit | Earlier full-area loading | Conditional inputs |
| --- | ---: | ---: |
| Distance only, 30.43 km | 6,591 ms, complete | 235 ms, complete |
| Shaped, 38.54 km | 15,526 ms, deadline | 1,047 ms, complete |
| Hills High, 40.83 / 38.32 km | 15,352 ms, deadline | 1,246 ms, complete |
| Moved endpoint, 40.71 / 41.97 km | 15,361 ms, deadline | 1,170 ms, complete |

All three new repeated samples complete for every edit. The shaped case spends
457 ms preparing, 384 ms planning, and 15.8 ms filling selected-route environment
observations. Hill and moved-endpoint preparation medians are 428 and 430 ms;
their diagnostic completion takes 30.0 and 26.3 ms. Full-area environment
attachment and signal reads have zero calls in these requests. Topology loading
and merged-area index assembly still occur.

The first request of the fresh worker is distance-only and takes 1.66 s. The
remaining first-pass edits take 1.60, 1.30, and 1.15 s, inheriting their predecessors'
application/OS cache state. These do not establish cold-disk latency. Simultaneous
distance-only and moved-endpoint requests have medians of 419 and 1,387 ms and
all complete. Concurrent phase instrumentation remains disabled.

Peak RSS falls from 2,475 MiB in the earlier configured-environment run to
762 MiB here. This is process peak memory, including transient preparation;
retention accounting is a different quantity. OS cache, CPU frequency, and
first-request storage state are uncontrolled, and sample counts are small.
Times include response construction and JSON serialization, excluding HTTP,
browser rendering, debounce, and import/startup.

## Remaining work

The 38.54 km shaped case remains slightly above the one-second backend duration,
before browser delivery. Some hill/moved candidates exceed 40 km. This report
does not declare the interaction target met. Intermediate dragged-route pins
remain unimplemented.

Enabled environment/signal channels still load area-wide data. The next packed
input artifact must include directional hill totals and partial detail alongside
environment intervals/support and signal gates/movements. Existing numeric hill
storage is a compatibility representation, not a separate architectural category.
The earlier fully cached mixed-preference search bottleneck is not optimized by
this change; it remains a workload for the landmark experiment.

## Reproduction

```sh
uv run --no-sync python -m runmap.experiments.benchmark_routing_edits \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --scenarios docs/archive/history/2026-11-12-routing-edit-scenarios.json \
  --cache-mib 128 --repeats 3 --concurrency 2 \
  --environment data/paths/prepared/helsinki-capital-area/environment-join.sqlite \
  --environment-raster-bundle data/environment/prepared/helsinki-capital-area-v3 \
  --report artifacts/conditional-inputs-2026-11-12/environment-off-128.json
```

Rebuild the native extension before running; no source artifact migration is
needed. The `route_input_diagnostics` phase covers deferred native observations;
other preparation timers remain nested as described in the earlier report.

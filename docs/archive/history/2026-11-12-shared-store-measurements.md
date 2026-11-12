# Routing edits after shared chunk reuse — 2026-11-12

The [machine-readable summary](2026-11-12-shared-store-measurements.json) preserves
individual samples, cache states, phase/call counts, statuses, candidate lengths,
and fingerprints. Full local captures are in
[`artifacts/shared-store-measurements-2026-11-12`](../../../artifacts/shared-store-measurements-2026-11-12).

## Scope and inputs

Routing core: `104e343`, with the benchmark instrumentation added alongside this
report. The normal release extension SHA-256 is
`5bc1ec992bfe8816b30b55c295c1bee1154d82fa5eb6146c8efda758ef9a5e59`.
Hardware is an Apple M5 with 16 GiB RAM, macOS ARM64, CPython 3.14.6, and
SQLite 3.53.4. Measurements run sequentially in separate worker processes per
configuration. OS cache and CPU frequency are uncontrolled.

Network dataset:
`2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`.
The matching numeric artifact and `signals.sqlite` are beside `network.sqlite`.
Environment controls use the identity-validated `environment-join.sqlite` and
`data/environment/prepared/helsinki-capital-area-v3` raster bundle. No datasets,
routing algorithms, native build flags, or server defaults were changed.

The [ordered scenarios](2026-11-12-routing-edit-scenarios.json) use home
`(24.795, 60.216)` and turnaround `(25.03, 60.25)`. They first generate a
distance-only route, enable default shaping, enable hills High, and then move
the turnaround to `(25.028, 60.251)` while keeping hills High. This measures
endpoint edits; intermediate dragged-route pins remain unimplemented.
The [mixed-preference scenario](2026-11-12-routing-mixed-scenarios.json) enables
hills, signals, greenery, water, and traffic at Low.

Only the first request of a worker starts with empty application caches. The
remaining first-pass requests inherit their predecessors' data. Three subsequent
ordered passes measure edits with the same history, rather than isolated identical
requests; the separate mixed-preference control uses five passes. Optional
concurrent batches then submit the distance-only and moved-pin
cases together, three times, through the existing two planning slots. They omit
global phase instrumentation to avoid patching functions across request threads.

Timings include response construction and JSON serialization, but exclude HTTP,
browser rendering, debounce, and import/process startup. These few samples are
descriptive observations, not production percentiles or cold-disk measurements.
The configured request deadline remains 15 seconds, with the default 650,000
segment cap, five retries, and 2,500 km² area limit.

## Topology and signal-data controls

| Edit | Candidate lengths | Repeated median, 128 MiB | Repeated median, 1 GiB |
| --- | --- | ---: | ---: |
| Distance only | 30.43 km | 428 ms | 306 ms |
| Default shape | 38.54 km | 1,421 ms | 882 ms |
| Hills High | 40.83 / 38.32 km | 1,799 ms | 711 ms |
| Moved endpoint, hills High | 40.71 / 41.97 km | 1,669 ms | 1,379 ms |

The 38.54 km case is within the agreed 40 km envelope and exceeds one second
before transport or browser rendering at the default cache size. Some hill and
moved-endpoint candidates exceed 40 km; those are supplementary stress cases,
not evidence of acceptance for an exclusively sub-40 km workload.

Completed shaped requests use two windows with 412,622 and 635,917 selected
segments. The final candidate counters report 190,750 / 201,157 expansions with
default shaping, 276,145 / 304,267 with hills High, and 720,612 / 771,074 in the
mixed-preference case. These counters aggregate candidate legs, not unique map
nodes; signal-aware traversal also expands arrival states. They are not totals
across all retries and must not be summed as disjoint candidate work.

At 128 MiB, the shaped request spends a median 926 ms in preparation and 387 ms
in planning. Nested within preparation, chunk access takes 243 ms, including
81 ms reading missing payloads and 157 ms preparing native chunks. Signal reads
take 403 ms even though signal avoidance is Off. The moved-endpoint request
spends 918 ms preparing and 602 ms planning.

At 1 GiB, all 3,261 required topology chunks remain resident, accounting for
274,413,976 bytes. Repeated chunk access falls to roughly 1 ms without payload
reads or native chunk preparation. New-area work remains: the moved request still
spends 677 ms preparing, including 400 ms reading signals, and 597 ms planning.
The hill-setting change can reuse exact-area graphs warmed by its predecessor;
its preparation median is under 1 ms. This shows why a fast repeated identical
request does not establish fast endpoint edits.

At 128 MiB, each repeated shaped request fetches and prepares 5,152 chunk payloads
across its two windows. At 1 GiB that count is zero. Shared ownership works when
chunks remain resident; a sequential working set larger than the chunk budget can
still churn through the LRU.

Phase timers are nested: chunk access includes payload reads and native chunk
preparation; numeric preparation also includes area assembly and optional cost
attachment. Signal conversion into native maps is outside the signal-read timer.
Independent phase medians need not sum to the median full request. Timers run
at chunk/coarse boundaries and do not instrument visited edges.

Peak process RSS was 745 MiB at the default budget and 728 MiB with the 1 GiB
control. Retention accounting charges shared owners conservatively more than
once; it is not a live-RSS measure. These observations do not establish a general
memory relationship or a deployment cache recommendation.

## Configured environment data

These controls keep environment preferences Off; hills remain as specified in
the edit sequence.

| Edit | Repeated median, 128 MiB | Repeated median, 3 GiB |
| --- | ---: | ---: |
| Distance only | 6,591 ms, complete | 427 ms, complete |
| Default shape | 15,526 ms, deadline | 600 ms, complete |
| Hills High | 15,352 ms, deadline | 770 ms, complete |
| Moved endpoint, hills High | 15,361 ms, deadline | 754 ms, complete |

All three repeated default-cache shaped/hill/moved requests return deadline
responses without candidates. The distance-only request spends 5,619 ms attaching
environment inputs, versus about 20 ms planning. The shaped request spends
13,168 ms attaching environment inputs across attempted areas. Its planning phase
only covers completed work before expiry and must not be compared as a complete
search against the successful controls. The cooperative 15-second deadline can
return slightly after 15 seconds.

The 3 GiB diagnostic control eventually retains four exact-area graphs and all
required topology chunks, accounting for 2,372,190,998 bytes. Preparation then
takes about 1 ms on repeated cached edits. This does not fix first access: the
first distance-only request takes 7.27 s, the subsequent shaped edit 13.96 s,
and the first moved endpoint reaches its deadline at 15.34 s. A large cache can
mask preparation on already visited areas; it does not establish fast new-area
updates.

Peak RSS reaches 2,475 MiB with the default retention budget and 2,676 MiB in the
large-cache control. Transient preparation and active graphs are substantial even
when no exact-area graph fits the default cache. Cache accounting remains distinct
from process memory.

## Mixed preferences: search remains a bottleneck

The separate mixed-preference run uses the 3 GiB cache and enables hills, signals,
greenery, water, and traffic at Low. Both candidates are 39.63 km, within the
agreed distance envelope. The first request expires after 15.18 s. The next
request completes in 14.62 s, including 12.34 s of preparation; it is still filling
the cache. The four subsequent requests complete in 2.66, 2.22, 2.22, and 2.21 s.

Those last four fully cached samples have a 2.218 s median, including 2.102 s
planning and 0.483 ms preparation. Their response fingerprints match, and all
five completed repeated requests agree. Preparation improvements alone therefore
cannot meet the target for this request. A* lower-bound experiments have a
measured preference-heavy workload to address after loading costs are separated.
The report preserves the initial expiry and slow cache-filling request rather
than labeling every repetition as already warm.

## Concurrent requests

Median times for simultaneous distance-only / moved-endpoint requests were
487 / 2,192 ms without environment inputs at 128 MiB and 344 / 1,474 ms at
1 GiB. With environment inputs and 128 MiB, they were 13,470 ms / 15,052 ms;
the moved request always expired. With the 3 GiB environment control, both completed
at 121 / 804 ms.

These batches follow the serial measurements and disable phase instrumentation.
Their cache and connection history differs from the serial passes, so differences
must not be interpreted as parallel speedup ratios. They exercise the current two
planning slots, not sustained multi-user load or cancellation of superseded edits.

## Architectural implications

1. Prepare reusable compact environment and signal inputs. The largest measured
   avoidable cost is full-area attachment, including when preference weights are
   zero. Keep selected-route diagnostics and exact cost semantics intact.
2. Reduce area-wide index assembly and unrelated-load serialization. Resident
   topology alone still leaves hundreds of milliseconds of work on new bounds.
3. Prototype the planned stronger A* lower bounds against the cached mixed case,
   preserving costs, eligibility, overlays, and signal-state semantics. Planning
   consumes roughly 0.4–0.6 s for the shaped/hill controls and 2.1 s with mixed
   preferences. The distance-only control also shows why search improvements
   cannot remove multi-second preparation by themselves.

Browser-visible latency, intermediate-pin semantics, more starts, genuinely cold
storage, larger prepared coverage at fixed route size, sustained concurrency,
and a specified acceptance percentile remain outstanding. No result here declares
the one-second interaction target met.

## Reproduction

Run each configuration separately so they do not compete for CPU or storage:

```sh
uv run --no-sync python -m runmap.experiments.benchmark_routing_edits \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --scenarios docs/archive/history/2026-11-12-routing-edit-scenarios.json \
  --cache-mib 128 --repeats 3 --concurrency 2 \
  --report artifacts/shared-store-measurements-2026-11-12/no-environment-128.json
```

Repeat with `--cache-mib 1024` for the topology-resident control. Add
`--environment data/paths/prepared/helsinki-capital-area/environment-join.sqlite`
and `--environment-raster-bundle data/environment/prepared/helsinki-capital-area-v3`
for configured environment inputs. All input paths and preference values are
recorded in each report. Reports include per-request phases, payload-read and
chunk-preparation call counts, cache states, route fingerprints, statuses,
candidate lengths, expansion attempts, and peak RSS.

The configured-environment controls use `--cache-mib 128` and `3072` with the
same three passes and `--concurrency 2`. The mixed-preference control uses the
mixed scenario file, `--cache-mib 3072 --repeats 5`, and no concurrent batches.
The larger budgets are diagnostic controls, not recommended service settings.

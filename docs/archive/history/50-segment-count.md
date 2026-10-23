# Spatial-index admission counts

Measured on 2026-10-23 with macOS 26.6.2 ARM64 and Python 3.14.6.
[The report](50-segment-count.json) preserves dataset/feature identities,
request settings, and timing summaries. Both implementations used the same
policy-union numeric artifact from [the pruning comparison](49-policy-union-pruning.md).

## Change

Run planning and distance exploration now count candidate segments directly in
`segment_bounds`, stopping at the configured cap plus one. Each exported source
segment has one spatial-index entry. The previous query joined every matching
entry to the segment record table; those lookups are unnecessary for counting.
Envelope predicates, crossing-segment inclusion, and rejection thresholds are
unchanged. Counts still include source candidates forbidden by routing policies;
they are admission checks, not eligible-graph counts.

The shared function is `paths.database.count_network_segments`. Both numeric
and reference planners use it, as do both exploration backends. The latency
benchmark now records the independent `segment_count` phase.

## Observations

Complete run requests include JSON serialization and exclude HTTP/browser work.
Each case used a fresh application graph cache, one first request, and three
repeated requests. The legacy SQL was measured first and OS cache state was
uncontrolled. Small samples establish a local diagnostic comparison, not a
production latency distribution or cold-disk guarantee.

| Hills High request | Before repeated median | After repeated median |
| --- | ---: | ---: |
| Short complete request | 40.46 ms | 30.28 ms |
| Short segment-count checks | 9.72 ms | 1.10 ms |
| Long complete request | 2,729.75 ms | 1,585.04 ms |
| Long segment-count checks | 982.74 ms | 54.86 ms |

All complete response hashes matched across implementations and repetitions,
excluding only elapsed time. Routes, costs, observations, counts, and expansion
metadata are identical. The long-request median improved by approximately 42%.
Payload loading and graph preparation remain the dominant long-request cost.
No exploration latency comparison was performed in this experiment.

All 339 Python tests pass. New count checks compare complete source-envelope
selection against capped counts for empty, crossing, local, and complete windows.
Existing planner/exploration tests exercise area limits and retries.

Raw phase samples and the local comparison script are retained under
`artifacts/pathfinding-profile/segment-count/`. Reproduce locally with:

```sh
uv run python artifacts/pathfinding-profile/segment-count/benchmark.py
```

## Next graph-reduction assessment

A separate read-only audit of the retained policy union found 123,943 recursively
peeled segments out of 586,218 (21.1%). These contained 530,715 hill intervals and
393,814 geometry points. This is an upper bound: endpoint branches and source
boundary uncertainty still need protection. Default-profile-only peeling found
22.5% before the policy-union export; those percentages describe different graphs.

The retained artifact has 2,616,596 hill intervals, whose chainage and directional
features alone occupy 334,924,288 bytes (57.5% of numeric payload bytes).
Separating interval detail from full-segment search totals therefore offers a
larger potential loading reduction than leaf peeling alone. Partial endpoint
traversals still need exact interval data; exploration needs all reachable
branches. Neither reduction is implemented by this admission-query change.

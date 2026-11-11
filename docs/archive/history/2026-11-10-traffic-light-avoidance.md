# Traffic-light avoidance smoke check — 2026-11-10

This is implementation evidence, not surveyed crossing attribution, regional
calibration, or performance acceptance. Current contracts are in
[traffic-light avoidance](../../traffic-light-avoidance.md).

## Inputs and preparation

- Network dataset: `2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`.
- Original source: October 25 Overpass JSON, SHA-256
  `d3e549ef9618efe06bb7a4ce96b5e511de3154fd946329735cbc857f27ca28f9`.
- Passage model: `directed-signal-passages-v1`; sidecar SQL schema 1.
- Detour weights: `signal-detour-metres-v1`, Low=60, High=180 equivalent metres.
- Local macOS arm64, CPython 3.14.6, release native extension built with maturin.

The [preparation command](../../development.md#prepare-traffic-light-passages)
produced a 9,400,320-byte sidecar without rebuilding network, terrain, environment,
or numeric graph data. It retained 31,165 node and 21,022 way evidence records.

| Prepared records | Count |
| --- | ---: |
| Crossing-way stages | 1,282 |
| Adjacent-node candidate stages | 390 |
| Crossing-node stages | 887 |
| Resolved gates | 1,290 |
| Unresolved gates | 382 |
| Resolved directed junction movements | 1,500 |
| Unresolved directed junction movements | 282 |

These counts describe prepared representations, not independent real-world lights.

## Archived paired-node fixture

The earlier audit's `w98279693p1` still exists in this network as segment number
202300, length 7.474 m. A smoke request used offsets 10% and 90% of its physical
chainage, a bounding rectangle around the segment's projected geometry enlarged
by 500 m in each direction, no shape/hill/environment costs, and the default
all-surfaces policy with stairs allowed.

| Signal penalty | Generalized route cost | Resolved / unresolved passages |
| --- | ---: | --- |
| Off | 5.9792 | unavailable |
| 60 m | 65.9792 | 1 / 0 |
| 180 m | 178.0828 | 0 / 0 |

High selected a detour rather than paying for the short crossing. Zero recognized
events on that detour does not establish complete mapping or signal-free travel.
One graph load contained 1,444 native nodes and an estimated 3,392 bytes of signal
maps. Measured load time was 19.24 ms; individual native searches took roughly
0.03–0.04 ms. These single samples do not establish p50/p95 or cold/warm guarantees.

To reproduce, export the matching sidecar, find the fixture's number and geometry
through `segments JOIN segment_features`, and load its expanded bounds with
`load_numeric_graph(..., signal_connection=...)`. Call `numeric_shortest_path`
using `NumericEndpoint(segment_id=202300, graph_offset_m=7.474*f)` for fractions
0.1 and 0.9 and signal weights 0, 60, and 180. Keep all other weights at zero.
Repeat with fresh request costs and record cache states for a real benchmark.

Synthetic tests additionally cover passage direction, gate splitting, exact
boundary starts/stops/reversals, repeated passages, leg seams, arrival-dependent
optimal routes, unavoidable/uncertain candidates, artifact identity, and API
results through both SQLite reference and native backends. Regional attribution,
calibration, performance, and runner acceptance remain in the roadmap.

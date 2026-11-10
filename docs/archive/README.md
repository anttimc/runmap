# Historical documentation

These documents preserve design discussions, implementation history, and dated
measurements. They are not the current product specification or task list.
Read the [guide](../guide.md), [architecture](../architecture.md), and
[roadmap](../roadmap.md) for current behavior and outstanding work.

The numbered active documents were consolidated on 2026-10-20 into topic pages.
Their original status text is retained beneath archive notices; links were
updated for the new locations. Commands are historical records and may contain
old setup instructions, output paths, or removed commands. Use
[current development instructions](../development.md) for new runs. Keep new
measurements separate from historical reports, and identify their dataset/model.

## Planning and implementation history

The [2026-11-04 route-mode UI review](history/2026-11-04-route-mode-ui.md)
records basic browser checks, user acceptance of current routing, and deferred
UI review.

The [environment acquisition](history/2026-11-02-environment-acquisition.md) and
[source inspection progress](history/2026-11-02-environment-inspection.md) record
the Leppävaara pilot, browser checks, road-class findings, and remaining review.
The [first environment raster baseline](history/2026-11-02-environment-rasters.md)
records the four fields, revised source rules, artifact identities, and checks.
The [reference-car noise update](history/2026-11-02-road-noise-proxy.md) records
inverse-distance spreading, dB normalization, and outside-context support.
The [separate park layer](history/2026-11-04-park-layer.md) records park coverage,
the nature-reserve distinction, and overlap/inspection checks.
The [greenery and water update](history/2026-11-04-greenery-and-water.md) records
weaker within-polygon park/reserve greenery and inclusion of all water polygons.

The [2026-11-02 native routing profile](history/2026-11-02-native-routing-profile.md)
separates Rust phase timings and allocation traffic, with reproduction commands
and links to generated visual reports in `artifacts/`.

The [2026-11-02 integration comparison](history/2026-11-02-integration-comparison.md)
compares spacing caps and minimum sample counts against a fine reference on real
geometry, with integration timings and visual speed–accuracy results.

The [multi-start loop-width sweep](history/59-multi-start-loop-width.md) measures
mean physical loop length and variation against width at five starts and audits
the runtime ellipse allowance with matched turnaround cohorts.
The [extended sweep and polynomial fits](history/60-extended-width-polynomials.md)
add 5 km and 10 km turnarounds, larger offline search windows, and whole-start
and whole-radius validation with Lauttasaari retained.

All supporting JSON, PNG, and SVG artifacts are beside their reports in
`history/`. Recorded numbers describe the identified historical dataset and
model; they do not prove current correctness, performance, or runner acceptance.

| Area | Historical documents |
| --- | --- |
| Direction | [Goals](history/01-goals.md), [milestones](history/02-milestones.md), [map-first plan](history/22-map-first-running-plan.md) |
| Design | [Architecture](history/03-architecture.md), [data and analysis](history/04-data-and-analysis.md), [decisions](history/05-decisions.md), [code-style work](history/23-code-style-plan.md) |
| Terrain | [Original experiment](history/06-leppavaara-terrain.md), [downloads](history/07-download-data.md), [smoothing results](history/08-smoothing-comparison-results.md), [interactive map](history/09-interactive-map.md), [region/hosting design](history/11-region-and-hosting-design.md) |
| Paths | [Graph prototype](history/13-path-graph-prototype.md), [path exploration](history/23-m4-path-exploration.md), [building-access plan](history/24-building-access-filter.md) and [results](history/24-building-access-results.md), [tile plan](history/25-tiled-path-rendering.md) and [results](history/25-tiled-path-results.md) |
| Routing | [Anchor baseline](history/14-diverse-route-baseline.md), [smooth-loop experiment](history/26-smooth-loop-routing-experiment.md), [first planner UI](history/27-first-routing-ui.md), [UI/settings](history/28-planner-ui-and-settings.md), [preferences](history/29-routing-preferences.md), [source audit](history/30-routing-preferences-audit.md), [cost architecture](history/31-routing-cost-architecture.md), [hill calibration](history/32-hill-routing.md) |

The [first Rust integration kernel report](history/33-rust-integration-kernel.md)
records the native boundary, initial validation, and real-route benchmark.
The [Python integer-index report](history/34-integer-search-index.md) measures the
following data-layout change independently of the Rust integrator.
The [packed-hill storage report](history/35-packed-hill-storage.md) records the
first migration of feature storage onto that shared integer ordering.
The [integer planner-cost report](history/36-indexed-planner-costs.md) records the
following migration of callbacks, endpoint parts, and request caches.
The [indexed exploration report](history/37-indexed-exploration.md) records bounded
Dijkstra, shared immutable distance buffers, and unchanged tile output.
The [packed measurement report](history/38-packed-measurements.md) records the
following migration of retained samples and integer display references.
The [packed geometry report](history/39-packed-geometry.md) records the following
WGS84/projected coordinate migration and capital-area graph memory comparison.
The [packed arc report](history/40-packed-arcs.md) records numeric arc/adjacency
storage, shared policy masks, and the memory/search-time tradeoff.
The [loop-length grid report](history/54-loop-length-grid.md) records the first
turnaround/width parameter-space sample, length surfaces, width sweeps, and
planner failures before choosing a surrogate model or automatic search method.
The [loop-length baseline report](history/55-loop-length-baselines.md) compares
polynomial and network-informed means with buffered spatial holdouts and maps
the remaining residual structure.
The [Pasila repetition](history/56-pasila-loop-length.md) repeats the grid and
six-model spatial validation at a second start, with comparable settings and
side-by-side errors against Leppävaara.
The [first MAP mixture report](history/57-loop-length-mixture.md) compares one
through four polynomial experts on the Leppävaara spatial holdouts.
The [RBF gate report](history/58-rbf-loop-length-gate.md) compares two nonlinear
gate scales with the linear gate and plots categories at two widths.
The [environment routing profile](history/2026-11-09-environment-routing-profile.md)
records preference-induced window expansion, graph-cache eviction, interval
loading costs, and native search hotspots on the local Helsinki dataset.
The [native search hot-loop comparison](history/2026-11-10-native-search-hot-loop.md)
records incremental optimization timings, exact output comparisons, and rejected
cache and edge-passing experiments on a fixed working area.

The terrain and routing mathematics were moved into maintained
[reference pages](../README.md), rather than archived.

## Superseded cycle-first designs

Archived 2026-10-09. Their unfinished tasks are not scheduled. Their
cycle-specific implementation was removed; the directed graph, shared policy
helpers, shortest-path queries, and CLI anchor baseline remain.

- [12: Original route-planning design](12-route-planning-design.md)
- [15: Cyclic topology](15-cyclic-topology.md)
- [16: Face-region loop search](16-face-region-loop-search.md)
- [17: Route profiles and superfaces](17-route-profiles-and-superfaces.md)
- [18: Cycle-catalog route search](18-cycle-catalog-route-search.md)
- [19: Cycle-catalog preprocessing](19-cycle-catalog-preprocessing.md)
- [20: One-cycle search](20-one-cycle-search.md)
- [21: Continuous cycle refinement](21-continuous-cycle-refinement.md)

## Code recovery

Commit `99141157453d0363889957821ecc921313ddcb86` contains the cycle-first
implementation before cleanup, including configuration, tests, plots, and
original documentation:

```sh
git show 99141157453d0363889957821ecc921313ddcb86:src/runmap/routing/microcycles.py
git worktree add --detach ../runmap-cycle-history 99141157453d0363889957821ecc921313ddcb86
```

Earlier milestones are `37f6b34` (cyclic topology), `0f42cdf` (face-region
search), and `d30d060` (profiled shape-aware search). Removed commands are
`routes topology`, `routes faces`, `routes catalog`, `routes atomic`, and
`routes microcycles`.

Git does not contain ignored source data or generated artifacts. Historical
reproduction needs compatible local data or preparation using the historical
configuration. Existing ignored cycle artifacts are not consumed by current
commands.

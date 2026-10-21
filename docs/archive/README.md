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

# Archived cycle-first plans

Archived 2026-10-09. These documents are historical evidence, not implementation
instructions. All unfinished work in them is superseded by the
[map-first plan](../22-map-first-running-plan.md). Historical status text is
preserved beneath an archive notice in each document.

- [12: Original route-planning design](12-route-planning-design.md)
- [15: Cyclic topology](15-cyclic-topology.md)
- [16: Face-region loop search](16-face-region-loop-search.md)
- [17: Route profiles and superfaces](17-route-profiles-and-superfaces.md)
- [18: Cycle-catalog route search](18-cycle-catalog-route-search.md)
- [19: Cycle-catalog preprocessing](19-cycle-catalog-preprocessing.md)
- [20: One-cycle search](20-one-cycle-search.md)
- [21: Continuous cycle refinement](21-continuous-cycle-refinement.md)

## Code recovery

Commit `99141157453d0363889957821ecc921313ddcb86` contains the implementation
before cleanup, including its configuration, tests, plots, and original docs:

```sh
git show 99141157453d0363889957821ecc921313ddcb86:src/runmap/routing/microcycles.py
git worktree add --detach ../runmap-cycle-history 99141157453d0363889957821ecc921313ddcb86
```

Earlier implementation milestones are `37f6b34` (cyclic topology), `0f42cdf`
(face-region search), and `d30d060` (profiled shape-aware search).

Removed commands: `routes topology`, `routes faces`, `routes catalog`,
`routes atomic`, and `routes microcycles`. Removed modules cover planar topology,
cycle topology, face search, cycle catalogues, atomic regions, microcycles, face
dissolution, and their diagnostic plots/tests. Access/surface/stair profile
helpers, path preparation, shortest-path queries, and `routes baseline` remain.

Git does not contain ignored source data or generated artifacts. Historical
reproduction needs compatible local data or preparation with the historical
configuration. Existing ignored files were not deleted by this cleanup.

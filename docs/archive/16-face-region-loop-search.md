# 16. Face-region loop search

> **Archived 2026-10-09 — not an implementation plan.** Superseded by the
> [map-first plan](../22-map-first-running-plan.md). Status statements and commands
> below describe the historical experiment. Its cycle-specific code was removed;
> see [archive recovery instructions](README.md).

## Purpose

Step 5 tests whether bounded planar faces are useful construction units for
running loops. It is deliberately an experiment rather than a general cycle
solver: grow connected face regions, retain regions with one simple boundary,
turn that boundary into a legal directed route, and compare it with the
anchor-based baseline under the same route constraints.

## First search formulation

The search:

1. chooses twelve faces whose boundary vertices are closest to the snapped
   start through the runnable graph;
2. grows each seed by adding adjacent faces, using six deterministic variants
   that trade boundary-length progress against compactness and jitter;
3. combines face edges by symmetric difference, so internal shared edges
   cancel;
4. accepts only a connected degree-two boundary containing every remaining
   edge, which gives one simple undirected cycle without holes;
5. tries both legal directed orientations and keeps the lower-ascent one;
6. reaches the cycle by a shortest access path and reverses that path after the
   cycle, exposing the unavoidable repetition in route metrics; and
7. ranks candidates with the baseline's distance, ascent, stairs, and repeated
   fraction rules, while limiting overlap among reported boundaries.

If pedestrian direction rules prevent reversing the access path, a penalized
shortest return is used as a fallback. Structural connector corridors are
available to access search but are not yet transitions between face regions.

The grow-only constraint is important. It makes every state explainable and
keeps the first comparison small, but it cannot remove an awkward face after
adding it or repair a locally jagged boundary.

## Command and outputs

Prepare the graph and topology first, then run:

```sh
PYTHONPATH=src .venv/bin/python -m runmap.cli routes faces \
  leppavaara-flat-10k
```

Ignored local outputs under `data/routes/prepared/leppavaara-flat-10k/` are:

- `face-regions.json`: selected face IDs, boundary segments, directed arc
  groups, generation settings, constraints, and metrics;
- `face-regions.geojson`: the selected complete routes for map inspection.

The diagnostic plot is written locally as `artifacts/face-region-routes.png`.

## First measured result

With a budget of 250 candidates distributed across all seed/variant pairs, the
search attempted and evaluated 232 usable single-cycle regions. Of these, 166
satisfied the 9.5–10.5 km distance window and 10% repetition limit. A repeated
run produced identical alternatives. Runtime was 13.7 seconds on the current
development machine.

| Alternative | Distance | Ascent | Repeated | Stairs | Faces |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 9.56 km | 115.2 m | 1.7% | 15.6 m | 31 |
| 2 | 10.14 km | 116.4 m | 1.6% | 21.3 m | 23 |
| 3 | 9.54 km | 117.2 m | 1.7% | 27.3 m | 32 |
| 4 | 9.52 km | 119.2 m | 1.7% | 19.7 m | 22 |
| 5 | 10.13 km | 119.5 m | 2.5% | 21.3 m | 22 |

The best face and anchor candidates compare as follows:

| Method | Distance | Ascent | Repeated | Stairs |
| --- | ---: | ---: | ---: | ---: |
| Connected faces | 9.56 km | 115.2 m | 1.7% | 15.6 m |
| Diverse-path anchor | 10.41 km | 105.6 m | 0.0% | 2.7 m |

Both meet the hard constraints. The anchor baseline currently wins the stated
flatness objective and avoids most stairs. Face search provides more explicit
control over enclosed geographic regions and produces genuinely different
route shapes, but its raw boundaries often contain small zigzags and detours
around fine-grained faces.

## Evaluation

The experiment supports extending face search, but not replacing ordinary
shortest-path routing with it. Its strongest role is as a high-level shape
generator whose boundary is later simplified and refined on the full directed
graph.

The next useful improvements are:

- allow face removal and boundary-preserving swaps after initial growth;
- penalize turns, stairs, tiny faces, and boundary complexity during growth;
- allow connector corridors to join planar face regions across bridges and
  underpasses;
- replace the reversed access stem when a good distinct return exists; and
- locally replace jagged boundary sections with better runnable paths while
  preserving the intended region and route length.

These belong to step 6 and should be added one at a time against visible route
examples.

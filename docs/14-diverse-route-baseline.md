# 14. Diverse-path route baseline

## Purpose

The first route generator is a general-graph baseline for both planning modes:

- for a required destination, find an outbound path and penalize its physical
  segments while finding the return path;
- for an environment-first loop, choose distributed intermediate anchors and
  apply the same outbound/return search.

This is the retained general-graph starting point for the
[map-first plan](22-map-first-running-plan.md). Guided assembly precedes further
automatic route generation.

## Search model

The implementation uses Dijkstra search over directed arcs. An arc's initial
cost combines length, directional ascent, and a strong stairs multiplier. The
return search multiplies the cost of physical segments used outbound. Vehicle
`oneway` tags do not affect pedestrians; explicit pedestrian direction rules
were already applied while preparing the graph.

Candidate generation varies ascent and return-overlap costs rather than
pretending one weight is final. The first experiment uses:

- ascent cost: 5, 20, and 50 metres of path cost per metre climbed;
- stairs multiplier: 8;
- outbound-segment return multiplier: 3, 10, and 30;
- 24 requested anchors distributed by bearing and approximate network distance.

These are search parameters, not the final route score. Completed loops are
checked against the 9.5–10.5 km distance range and 10% repeated-traversal limit.
Feasible candidates are ranked primarily by measured ascent. Returned
alternatives prefer different anchors before showing another variant through
the same anchor.

Repeated distance counts every traversal of an undirected segment that occurs
more than once. The short degree-one access branch at Ruusutorpan koulu is
therefore visible in both repeated distance and an explicit start-access
reversal field.

## Study extent

The experiment has no hard Ring I boundary. Routes and topology may use any
pedestrian-connected crossing inside the prepared graph, including tagged
underpasses and bridges. The smaller face-test bounds define the current
topology study area without changing graph connectivity.

## Commands and outputs

Rebuild the path graph whenever the experiment configuration changes, then run
the baseline:

```sh
PYTHONPATH=src .venv/bin/python -m runmap.cli paths prepare \
  leppavaara-flat-10k
PYTHONPATH=src .venv/bin/python -m runmap.cli routes baseline \
  leppavaara-flat-10k
```

The ignored local output under `data/routes/prepared/leppavaara-flat-10k/`
contains:

- `diverse-baseline.json`: arc IDs, anchors, generation settings, constraint
  violations, and route metrics;
- `diverse-baseline.geojson`: the selected alternatives for map inspection.

## First measured result

The updated search evaluated 142 distinct loops through 24 reachable anchors.
Twenty-one satisfied both hard constraints. The five reported alternatives
were:

| Alternative | Distance | Ascent | Repeated |
| --- | ---: | ---: | ---: |
| 1 | 10.41 km | 105.6 m | 0.0% |
| 2 | 10.45 km | 107.0 m | 1.1% |
| 3 | 10.04 km | 118.9 m | 2.1% |
| 4 | 10.48 km | 123.8 m | 0.8% |
| 5 | 10.27 km | 138.6 m | 1.2% |

None contains an internal immediate reversal or a start-access reversal. Ring
I does not act as a route boundary; its pedestrian underpasses and bridges
remain available through normal graph connectivity.

These numbers establish a reproducible baseline, not a recommendation that the
first route is already the best real run. Surface quality, service-road
penalties, turns, surroundings, and closer visual inspection against a basemap
remain outside this step.

Future route and topology preview plots should use stronger basemap or landmark
context beneath the generated geometry so alternatives are easier to locate
visually.

## Evaluation

The baseline demonstrates that distinct outbound and return paths can satisfy
the first distance and repetition constraints on the real graph. It also shows
the method's limitations: anchor choice determines broad route shape, several
cost settings can converge on nearly the same loop, and path quality beyond
stairs is not yet scored.

The baseline experiment is complete. Its fixed distance/repetition thresholds
are evaluation settings, not universal product constraints. Surface/access
profile integration and UI guidance remain planned. The old cyclic-topology
follow-up is archived; continue with [path exploration](22-map-first-running-plan.md).

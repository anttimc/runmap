# 18. Cycle-catalog route search

> **Archived 2026-10-09 — not an implementation plan.** Superseded by the
> [map-first plan](../22-map-first-running-plan.md). Status statements and commands
> below describe the historical experiment. Its cycle-specific code was removed;
> see [archive recovery instructions](README.md).

> **Status: superseded.** This document records the standalone-cycle catalog
> experiment and is retained for its design history and measurements. Do not
> implement its remaining steps. The active cycle-search plan is
> [continuous cycle refinement](21-continuous-cycle-refinement.md).

## Motivation

A runner familiar with an area rarely invents a long route one street at a
time. They remember several good loops and route sections, then combine them
to obtain the desired distance and character. The next environment-first
planner should use the same separation of concerns:

1. preprocess the network outwards from a starting point into a reusable local
   catalog of good runnable cycles;
2. answer a route request by combining catalog entries; and
3. expand and validate the combination on the directed routing graph.

This avoids asking a target-distance heuristic to discover route quality and
route length simultaneously. Length belongs primarily to request-time search;
basic runnability and local cycle quality belong primarily to preprocessing.

## Two levels of optimization

### Profile-specific preprocessing

Preprocessing starts from a supplied starting point, the prepared graph,
topology, and a route profile. Stable hard constraints are applied here: legal
pedestrian access, excluded indoor networks, forbidden surfaces, and the
selected stair policy. The preprocessor explores reachable seeds in directed
access-distance order and generates cycles at several geographic and length
scales. Dual breadth-first layers support diagnostics and bounded experiments;
they are not access distances and do not require cycles to enclose the start.

Cycle sources can include:

- boundaries of individual faces and profile-specific superfaces;
- good merged-face regions found without a final route-length target;
- diverse-path cycles in non-planar or sparse parts of the graph; and
- later, manually accepted or frequently used cycles.

Every cycle is converted to its legal directed traversal alternatives and
measured once.
Poor cycles with forbidden edges, near reversals, excessive repetition, or
clearly unusable geometry are rejected. The remaining cycles are deduplicated
by boundary signature and selected using length-weighted overlap, quality, and
coverage, preserving geographic and terrain representatives. Hills and
environmental properties should
usually be recorded rather than optimized away: a flat-training request and a
hill-training request may legitimately choose different catalog entries.

Preprocessing is local to a starting point but must not be tied to the current
10 km fixture. It should produce short neighborhood loops and larger district
loops so several request lengths from the same start have useful building
blocks.

### Request-time composition

A request selects the catalog for its start and supplies the target length,
terrain goal, environment preferences, and repetition limits. Search first
filters catalog cycles by their precomputed outward and return access distances
from the start. It then searches over compatible cycle combinations rather
than individual faces.

An initial bounded beam search is sufficient. A search state contains the
selected cycle IDs, the composed edge signature, its current metrics, and the
access connection to home. Envelope states also retain the union of member
base faces, so overlapping interiors are handled correctly. Adding one catalog
cycle is a macro operation whose
distance and quality effects are already mostly known. The search score can
therefore focus on target-length error, total ascent, environment fit,
repetition, and diversity between returned alternatives.

The final state is expanded to directed arcs and passed through the existing
route validator. Exact distance, ascent, stairs, turns, and repeated edges are
always recomputed; catalog summaries are search accelerators, not a substitute
for validation.

## Cycle representation

Each catalog entry should contain:

- a stable ID and the start, graph, topology, profile, and algorithm versions;
- legal directed-arc alternatives and the undirected segment sequence;
- member face or source-cycle provenance;
- length, ascent, descent, stairs, surface, and environment summaries;
- turn, concavity, compactness, and repetition measurements;
- a compact edge signature for overlap tests;
- spatial bounds, candidate attachment vertices called portals, and their
  outward/return access summaries; and
- a quality/rejection explanation suitable for inspection.

The catalog is profile-specific because dissolving forbidden separators changes
which cycles exist. Metric fields can still support request-specific filtering
and scoring without rebuilding the topology for every target length.

## Joining cycles

There are two useful composition operations, and they should remain explicit:

1. **Envelope merge.** For adjacent or overlapping face-derived cycles, form
   the union of their member base-face sets and extract that union's boundary.
   Accept the merge only if it is one simple, legally traversable cycle without
   holes. XOR of the two input boundaries is valid for disjoint face interiors
   sharing boundary edges, subject to validation; with overlapping interiors
   it represents the symmetric difference, not the desired union. Implement
   face-union envelope merging first.
2. **Itinerary join.** Traverse two cycles through a shared portal or a short
   connector. This can produce a figure-eight or a controlled repeated access
   section rather than one simple boundary. It requires the planned directed
   closed-walk representation.

A compatibility index records which pairs can be joined, the operator, shared
segments or connector, estimated metric deltas, and any new turn or repetition
risk. Request-time search should not rediscover these pairwise facts.

Envelope length is not simply the sum of its cycles because shared edges are
removed. Itinerary length may include connector traversal twice. Composition
therefore uses stored pairwise deltas to propose joins, then recomputes the
boundary of the current face union. Pairwise deltas are not additive for three
or more overlapping regions. Exact final expansion remains mandatory.

## Prepared artifacts

The first file-backed prototype can use:

- `cycles.json`, containing normalized cycle records;
- `cycle-joins.json`, containing compatible pairwise joins; and
- `cycles.geojson`, supporting visual inspection and manual evaluation.

The cache key includes the snapped start, prepared graph digest, topology
version, route profile, cycle-generation settings, and scoring-schema version.
A database is unnecessary until catalog size or query latency demonstrates the
need for one.

## Implementation sequence

### 1. Extract a first catalog from existing machinery

- Separate cycle generation from the 10 km route request.
- Run deterministic bounded growth from access-ordered reachable seeds,
  collecting several size bands in one run and refining with add/remove/swap
  moves. Use the block-local linear oracle in plan 20 for comparison.
- Store validated directed cycles with provenance and metrics.
- Render the complete catalog and a quality-filtered subset.

**Evaluation gate:** recognizable runnable loops exist at multiple scales and
the catalog is deterministic, inspectable, and not dominated by near-duplicates.

**First result:** implemented for the Leppävaara/Kilo topology with 48 selected
cycles across four length bands. See
[cycle-catalog preprocessing](19-cycle-catalog-preprocessing.md).

### 2. Prune and diversify the catalog

- Apply stable profile constraints before insertion.
- Select greedily by quality and new coverage within geographic/terrain groups;
  defer exact catalog selection until measured shortcomings justify it.
- Cluster by length-weighted edge overlap, location, and length, retaining
  representative alternatives with meaningfully different terrain or environment.

**Evaluation gate:** catalog size stays manageable while preserving geographic
coverage and distinct useful choices.

### 3. Build envelope-merge compatibility

- Index cycles by shared segments and neighboring member faces.
- Validate boundaries of pairwise face unions, including overlapping interiors.
- Store join deltas, resulting signatures, and shape risks.

**Evaluation gate:** known compatible loops combine into legal larger cycles,
and incompatible pairs fail for an explainable reason.

### 4. Search combinations for the 10 km request

- Use the cataloged portal and access information for Ruusutorpan koulu.
- Beam-search cycle combinations using target length, flatness, repetition,
  and shape.
- Expand the best combinations to directed routes and validate exact metrics.
- Compare route quality and request latency with online face growth and the
  anchor baseline.

**Evaluation gate:** the planner constructs useful 10 km alternatives from
reusable cycles, improves route shape, and performs substantially less
request-time topology work than the current 40-second search.

### 5. Add itinerary joins only when needed

- Introduce the directed closed-walk/multiset representation.
- Support shared portals, short connectors, figure-eights, and controlled
  out-and-back access.
- Keep envelope and itinerary composition visible in result explanations.

**Evaluation gate:** the richer representation adds useful routes that cannot
be expressed as one simple boundary without obscuring repetition.

## Relationship to the current search and alternatives

The current shape-aware face beam remains valuable as:

- an offline generator of catalog cycles;
- a request-time fallback where the catalog has poor coverage; and
- a local refinement method after a catalog combination is selected.

A pure minimum-cycle-basis approach is a useful non-planar fallback but is not
the primary design: algebraically independent cycles are not necessarily good
running loops. Another alternative is a library of good open path corridors.
That representation may ultimately suit destination-first planning better,
but cycles are the more direct first abstraction for home-returning exploratory
runs.

The main risk is preprocessing bias. If only one definition of “good” is used,
the catalog can permanently discard cycles needed by another training goal.
Hard preprocessing filters should therefore be limited to genuine profile
constraints, while ascent, scenery, and similar preferences remain observable
dimensions in a diverse catalog.

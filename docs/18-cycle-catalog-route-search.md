# 18. Cycle-catalog route search

## Motivation

A runner familiar with an area rarely invents a long route one street at a
time. They remember several good loops and route sections, then combine them
to obtain the desired distance and character. The next environment-first
planner should use the same separation of concerns:

1. preprocess the network into a reusable catalog of good runnable cycles;
2. answer a route request by combining catalog entries; and
3. expand and validate the combination on the directed routing graph.

This avoids asking a target-distance heuristic to discover route quality and
route length simultaneously. Length belongs primarily to request-time search;
basic runnability and local cycle quality belong primarily to preprocessing.

## Two levels of optimization

### Profile-specific preprocessing

Preprocessing starts from the prepared graph, topology, and a route profile.
Stable hard constraints are applied here: legal pedestrian access, excluded
indoor networks, forbidden surfaces, and the selected stair policy. The
preprocessor then generates cycles at several geographic and length scales.

Cycle sources can include:

- boundaries of individual faces and profile-specific superfaces;
- good merged-face regions found without a final route-length target;
- diverse-path cycles in non-planar or sparse parts of the graph; and
- later, manually accepted or frequently used cycles.

Every cycle is converted to a legal directed traversal and measured once.
Poor cycles with forbidden edges, near reversals, excessive repetition, or
clearly unusable geometry are rejected. The remaining cycles are deduplicated
by edge overlap and retained as a diverse Pareto set across length, ascent,
shape, surface, and environment. Hills and environmental properties should
usually be recorded rather than optimized away: a flat-training request and a
hill-training request may legitimately choose different catalog entries.

Preprocessing must not be tied to the current 10 km fixture. It should produce
short neighborhood loops and larger district loops so request-time search has
useful building blocks.

### Request-time composition

A request supplies the start, target length, terrain goal, environment
preferences, and repetition limits. Search first finds catalog cycles that are
reachable from the start within a reasonable access budget. It then searches
over compatible cycle combinations rather than individual faces.

An initial bounded beam search is sufficient. A search state contains the
selected cycle IDs, the composed edge signature, its current metrics, and the
access connection to home. Adding one catalog cycle is a macro operation whose
distance and quality effects are already mostly known. The search score can
therefore focus on target-length error, total ascent, environment fit,
repetition, and diversity between returned alternatives.

The final state is expanded to directed arcs and passed through the existing
route validator. Exact distance, ascent, stairs, turns, and repeated edges are
always recomputed; catalog summaries are search accelerators, not a substitute
for validation.

## Cycle representation

Each catalog entry should contain:

- a stable ID and the graph, topology, profile, and algorithm versions;
- directed arc and undirected segment sequences;
- member face or source-cycle provenance;
- length, ascent, descent, stairs, surface, and environment summaries;
- turn, concavity, compactness, and repetition measurements;
- a compact edge signature for overlap tests;
- spatial bounds and candidate attachment vertices, called portals; and
- a quality/rejection explanation suitable for inspection.

The catalog is profile-specific because dissolving forbidden separators changes
which cycles exist. Metric fields can still support request-specific filtering
and scoring without rebuilding the topology for every target length.

## Joining cycles

There are two useful composition operations, and they should remain explicit:

1. **Envelope merge.** For adjacent or overlapping face-derived cycles, take
   the symmetric difference of their segment sets. Shared internal edges
   disappear and the result is one outer cycle. This matches the current
   topology model and should be implemented first.
2. **Itinerary join.** Traverse two cycles through a shared portal or a short
   connector. This can produce a figure-eight or a controlled repeated access
   section rather than one simple boundary. It requires the planned directed
   closed-walk representation.

A compatibility index records which pairs can be joined, the operator, shared
segments or connector, estimated metric deltas, and any new turn or repetition
risk. Request-time search should not rediscover these pairwise facts.

Envelope length is not simply the sum of its cycles because shared edges are
removed. Itinerary length may include connector traversal twice. Composition
therefore uses the stored join delta and still performs exact final expansion.

## Prepared artifacts

The first file-backed prototype can use:

- `cycles.json`, containing normalized cycle records;
- `cycle-joins.json`, containing compatible pairwise joins; and
- `cycles.geojson`, supporting visual inspection and manual evaluation.

The cache key includes the prepared graph digest, topology version, route
profile, cycle-generation settings, and scoring-schema version. A database is
unnecessary until catalog size or query latency demonstrates the need for one.

## Implementation sequence

### 1. Extract a first catalog from existing machinery

- Separate cycle generation from the 10 km route request.
- Run face-region generation over several size bands and locations.
- Store validated directed cycles with provenance and metrics.
- Render the complete catalog and a quality-filtered subset.

**Evaluation gate:** recognizable runnable loops exist at multiple scales and
the catalog is deterministic, inspectable, and not dominated by near-duplicates.

### 2. Prune and diversify the catalog

- Apply stable profile constraints before insertion.
- Pareto-prune clearly inferior cycles.
- Cluster by edge overlap, location, and length, retaining representative
  alternatives with meaningfully different terrain or environment.

**Evaluation gate:** catalog size stays manageable while preserving geographic
coverage and distinct useful choices.

### 3. Build envelope-merge compatibility

- Index cycles by shared segments and neighboring member faces.
- Validate pairwise symmetric-difference joins.
- Store join deltas, resulting signatures, and shape risks.

**Evaluation gate:** known compatible loops combine into legal larger cycles,
and incompatible pairs fail for an explainable reason.

### 4. Search combinations for the 10 km request

- Connect Ruusutorpan koulu to nearby catalog portals.
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

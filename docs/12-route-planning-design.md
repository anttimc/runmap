# 12. Route-planning design

## Purpose

Route planning serves two related but different running goals:

1. **Environment-first loop:** explore an area through a good running route,
   whether near home or somewhere unfamiliar. Start and finish at the same
   place while spending the run in terrain and surroundings that fit the
   intended training. The destination is the route and the environment it
   reveals.
2. **Destination-first outing:** reach and explore a particular place outside
   the usual home territory, then return, preferably by a different path. The
   destination is required and the return route should avoid unnecessary
   repetition.

Both modes should share one path network, terrain measurements, preference
model, route representation, and validation pipeline. They use different
candidate generators: cycle construction is the primary approach for the first
mode, while diverse outbound and return paths are the primary approach for the
second.

This is a staged design, not a commitment to implement the complete routing
system at once. Each step should produce inspectable routes and evidence before
the next abstraction is added.

## Route-planning model

A request contains:

- a start point and optionally a required destination point or area;
- a target distance or distance range;
- training preferences such as flat, hilly, or mixed terrain;
- environment preferences such as paths, parks, forest, waterfront, surface,
  traffic, or lighting;
- tolerances for repeated edges, access stems, and route complexity.

A result is a directed closed walk with per-segment provenance and a score
breakdown. Repeated segments remain visible in the result instead of being
hidden inside a single overall score.

The generic model should support both hard constraints and an explainable
weighted score. Distance range, legal traversal, and a required destination are
normally constraints; terrain, environment, repetition, turns, and route shape
can be promoted from preferences to constraints for a particular experiment.
This keeps experiments flexible while the quality model is still being learned.

## First experiment: flat 10 km Leppävaara loop

The first route-planning experiment is an environment-first loop with these
inputs:

| Setting | Initial value |
| --- | --- |
| Start and finish | Ruusutorpan koulu, Leppävaarankatu 24, Espoo |
| Start-node selection | Snap the landmark to the nearest sensibly accessible vertex on the prepared runnable network; record the chosen vertex and snap distance |
| Search area | Leppävaara and Kilo west of Ring I; define the exact preparation polygon with a buffer during graph extraction |
| Target distance | 10 km |
| Accepted distance | 9.5–10.5 km |
| Training goal | Minimize total ascent |
| Repeated distance | At most 10% of route length on repeated undirected edges |
| Immediate reversal | Disallow, except for an unavoidable access stem |
| Initially runnable | Pedestrian paths, pedestrian-permitted cycleways, residential and living streets, and suitable tracks |
| Excluded | Motorways, prohibited or private access, and disconnected geometry |
| Stairs | Allowed with a strong penalty |

Candidate comparison is lexicographic at first: reject illegal routes and
routes outside the distance or repetition limits, then minimize measured total
ascent, then prefer simpler and more natural route geometry. This prevents a
low-ascent route from winning by repeating one flat corridor.

For consistency, ascent will initially be measured by sampling the existing
smoothed terrain product along graph geometry. The sample spacing and ascent
aggregation rule belong to graph implementation and must be recorded with the
result; they are not fixed by this experiment definition.

Each candidate should report at least total distance, ascent, descent, repeated
distance, stair distance, surface categories, the snapped start vertex, and any
questionable access decisions. Manual evaluation asks whether the route is
legally runnable, geographically plausible, naturally shaped, and credible to
a runner familiar with the area.

This experiment intentionally covers only the environment-first mode. A
destination-first fixture will be defined after the shared graph and initial
route measurements can be inspected.

## Shared architecture

| Component | Responsibility |
| --- | --- |
| Network preparation | Extract runnable OSM ways, split them at real junctions, and preserve bridges, tunnels, access rules, and one-way restrictions. |
| Directed routing graph | Store vertices and travel arcs with distance, geometry, direction-dependent ascent, surface, safety, and environment attributes. |
| Embedded topology | Store the angular order and opposite side of geometric edges for areas where face traversal is valid. A half-edge/DCEL-like representation supplements the routing graph rather than replacing it. |
| Terrain and environment enrichment | Attach elevation profiles, ascent/descent, gradient characteristics, land-cover context, and other route-quality attributes to graph arcs. |
| Topology index | Identify bridges, the 2-core, cyclic components, biconnected regions, planar faces, and later any additional cycle basis. |
| Candidate generators | Produce loop candidates from cycles or face regions, and destination candidates from diverse path pairs. |
| Scoring and constraints | Compare distance, terrain, environment, overlap, turns, access stems, and road/path quality with an explainable score breakdown. |
| Refinement and validation | Adjust candidate length, orient and connect route pieces, enforce traversal rules, and reject disconnected or invalid walks. |
| Route API and UI | Express the two planning modes, show alternatives and tradeoffs, and allow the runner to inspect why a route was proposed. |

The graph and topology data should be prepared offline for a versioned region.
Route requests then search prepared structures and read cached segment
attributes. The first prototype can use in-memory data and files; a routing
database is not required until measured data size or query patterns justify it.

## Approach A: environment-first cycle construction

This mode searches for a loop near the start whose distance, terrain, and
environment fit the request. It supports discovery as well as training: a
runner visiting an unfamiliar area can ask for a representative, enjoyable
loop without already knowing which landmark or path to choose.

For a planar portion of the network, bounded road-network faces act as atomic
cycles. A candidate selects a connected set of faces. Its boundary is formed
with the symmetric difference of their edges, so an edge shared by two selected
faces cancels. Adding or removing a neighboring face becomes a local search
operation that changes route length and character.

The search has four broad stages:

1. Locate the start relative to the network's cyclic components. Treat bridges
   between the start and a cyclic component as explicit out-and-back access
   stems.
2. Seed one or more promising face regions based on distance and preferences.
3. Grow, shrink, or reshape each region and score its boundary.
4. Turn the selected boundary into a legal directed route and refine awkward
   turns, repeated sections, holes, and distance error.

Faces are not sufficient everywhere. Sparse rural graphs may have enormous
faces, and bridges, tunnels, or complex trail networks may not have a useful
planar-face interpretation. In those regions the same candidate interface can
use short cycles from a minimum or relevant cycle basis. The first experiment
should use face cycles in a small, well-understood area before adding that
fallback.

## Approach B: destination-first diverse paths

This mode constructs an outbound path from the start to the destination and a
return path from the destination to the start. The return search penalizes
undirected edges already used outbound, while still permitting unavoidable
shared access sections. It supports exploration beyond familiar home routes:
the runner chooses the more distant place they want to investigate, and the
planner makes the journey there and back part of the run.

The basic generator is:

1. Find a good outbound path using distance, terrain, and environment costs.
2. Find one or more return paths with an overlap penalty and optional corridor
   separation preference.
3. Join the two paths, measure actual repetition, and refine the pair together.
4. Offer alternatives that expose the tradeoff between path quality, total
   distance, and novelty.

This is not simply two independent shortest-path queries. The path pair is the
candidate, and its final score includes both legs. Algorithms such as repeated
shortest-path search with edge penalties, k-shortest paths, or disjoint-path
methods can be compared behind the same generator boundary.

The diverse-path method is also an alternative loop generator when face-based
construction is not suitable: choose one or more intermediate anchors, find
distinct paths between them, and connect the resulting cycle to the start. It
is likely to be the simplest general-graph baseline against which face-cycle
search should be evaluated.

## Implementation sequence

### 1. Define the smallest routing experiment

- Use the flat 10 km Leppävaara loop defined above as the first request.
- Confirm during graph inspection that the preparation area contains ordinary
  blocks, trails, and examples of bridges or dead ends.
- Defer a comprehensive preference model and the destination-first fixture.

**Evaluation gate:** complete. The first request and expected tradeoffs are
defined without depending on a routing implementation.

### 2. Build and inspect the shared directed graph

- Extract and normalize runnable OSM geometry.
- Add directed arcs, distance, elevation-derived attributes, and essential
  access metadata.
- Provide graph inspection output and tests for junctions, crossings, bridges,
  tunnels, and direction-dependent ascent.

**Evaluation gate:** complete for the first bounded experiment. The prepared
graph, start snap, path-class distribution, connectivity, and terrain coverage
were inspected; measured limitations are recorded in the
[path-graph prototype](13-path-graph-prototype.md).

### 3. Implement the diverse-path baseline

- Add ordinary shortest-path search over configurable arc costs.
- Generate outbound/return path pairs using an overlap penalty.
- Support a required destination and also an anchor-based loop experiment.
- Report distance, ascent, repeated distance, and the reason for each major
  penalty.

**Evaluation gate:** destination routes usually return by a sensible different
path when one exists, while unavoidable repetition remains allowed.

### 4. Prepare cyclic topology

- Compute bridges, the 2-core, and cyclic/biconnected components.
- Build the half-edge embedding for a bounded planar test area and enumerate
  face boundaries.
- Verify face traversal and shared-edge cancellation on synthetic graphs and
  several real neighborhoods.

**Evaluation gate:** the topology explains dead ends, access stems, and block
boundaries correctly before it is used for route search.

### 5. Prototype face-region loop search

- Seed connected face sets near the start and modify them with add/remove
  operations.
- Extract their symmetric-difference boundary and initially accept only a
  single usable boundary component.
- Rank candidates using the same scoring interface as the diverse-path
  baseline.

**Evaluation gate:** compare face-based and anchor-based loops for route shape,
distance error, repetition, terrain fit, runtime, and failure cases. Decide
whether face search merits extension before implementing a general cycle
basis.

### 6. Add route refinement and richer preferences

- Add local segment replacement, small-cycle insertion/removal, direction
  choice, and access-stem handling as demonstrated needs.
- Introduce surface, natural environment, traffic, lighting, and detailed hill
  preferences one at a time, with route-level examples.
- Keep every score component observable so weight changes can be evaluated.

**Evaluation gate:** preferences cause predictable route changes and do not
silently violate the core route request.

### 7. Integrate an exploratory UI

- Present environment-first and destination-first planning as separate modes.
- Show a small set of alternatives rather than claiming one optimum.
- Display length, ascent, repetition, surfaces, and preference tradeoffs, and
  allow rejected suggestions to become evaluation examples.

**Evaluation gate:** generated routes are useful enough to try in familiar
areas before geographic coverage or routing performance is expanded.

## Early non-goals

- Globally optimal routes for every combination of preferences.
- Turn-by-turn navigation or live rerouting.
- A final universal definition of scenic, safe, flat, or hilly.
- Eliminating every repeated edge; some starts and destinations make repetition
  topologically unavoidable.
- Supporting all nonplanar and directed-network corner cases in the first
  face-cycle prototype.

## Decisions to make from experiments

- Whether face regions produce better environment-first loops than the simpler
  diverse-path baseline often enough to justify their complexity.
- Whether a minimum/relevant cycle basis is needed outside planar urban areas.
- How strongly overlap should be penalized relative to surface, terrain, and
  total distance.
- Whether environment preferences belong mainly on arcs, surrounding
  corridors, or whole-route summaries.
- Which route refinements materially improve real runs rather than only making
  map geometry look tidy.

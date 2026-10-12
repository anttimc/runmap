# 22. Map-first running exploration and planning

Status: active high-level plan, adopted 2026-10-09. This supersedes the
cycle-first roadmap in archived plans 12 and 15–21. See the
[documentation index](README.md) and [milestones](02-milestones.md).

## Product direction

First help a runner understand where they could run, then help them assemble a
route, and finally suggest complete runs with their guidance. The foundation is
one attributed pedestrian graph. Terrain exploration, reachability, manual
selection, and automatic routing share its geometry and measurements.

```text
OSM connectivity + elevation
    -> attributed directed path graph
    -> path-gradient map and start-based exploration
    -> guided route assembly
    -> a few diverse automatic suggestions
```

Cycles remain useful visual examples and saved routes. They need not cover the
network, form a basis, have disjoint interiors, or compose through shared
boundaries. A cycle basis exists for general undirected graphs, but does not
provide a catalogue of intuitive running routes. Planar-face partitioning,
superfaces, cycle catalogues, merge hierarchies, and exact face-search solvers
are outside the implementation roadmap.

## Foundation and existing work

Keep the terrain pipeline and MapLibre map, the OSM path importer, directed
graph, terrain enrichment, shortest-path and distance queries, reusable edge
profile helpers, and anchor-based route baseline. The
[graph prototype](13-path-graph-prototype.md) and
[baseline evaluation](14-diverse-route-baseline.md) document working experiments.
Their existence does not mean path exploration or guided routing is already
available in the UI.

Preserve real OSM junctions and grade-separated connections. Geometric crossings
do not create junctions. Prepare data with context beyond the displayed area;
flag extract boundaries so artificial dead ends are not presented as facts.
Access, pedestrian direction, stairs, surfaces, and uncertainty belong to the
graph and its shared filtering policy. Existing profile helpers still need
integration into exploration and routing; the baseline is not a finished
implementation of that policy.

Preparation produces versioned graph geometry and sampled terrain attributes.
It does not depend on a start point or target distance. Start-dependent queries
run over prepared data. Do not send the full experimental graph JSON to the
browser; begin with bounded feature delivery and add vector tiles when measured
size and latency justify them.

## M4 — Explore path gradients and connections

The path map around Ruusutorpan koulu now includes gradient inspection and
start-based outing inclusion. M4.5 local review and the performance gate remain.
The [M4 implementation plan](23-m4-path-exploration.md) breaks this into
measurement preparation, map inspection, shared eligibility, start/budget
queries, and local evaluation. Its working defaults remain subject to review.

1. Colour short path sections by along-path gradient magnitude. Keep contours
   and terrain shading as optional context. Show uphill direction on selection
   or at an appropriate close zoom, with a clear legend.
2. Inspect sampled profiles, ascent/descent, sustained climbs, surface/access
   evidence, and uncertainty. Define and record smoothing, sample spacing, and
   climb aggregation. A whole-edge average must not hide a climb and descent.
3. Distinguish terrain slope from path grade. Ground elevation can misrepresent
   bridges, tunnels, and engineered paths; flag those estimates. Missing or
   unreliable measurements must not look flat. Existing gradient integration
   is a useful starting point, not a verified path-elevation profile.
4. Choose and visibly snap a start. Highlight paths reachable outward and back
   under the same access/profile rules. A flatter-path preference changes
   emphasis; it should not silently remove necessary hill connections.
5. Add a return-distance budget. For a directed edge from u to v, its minimum
   inclusion distance is outward_distance(u) + edge_length + return_distance(v).
   Consider legal directions and partial edges when needed. Vertex distances
   alone do not establish whether an entire edge fits the budget. This permits
   retracing and does not promise a simple loop or a route of exactly that length.

Ship gradient display and inspection before reachability if needed, keeping
both within this milestone. Validate familiar flat corridors, short and sustained
hills, a bridge/underpass, restricted access, and a disconnected path. Record
known data failures and measured viewport/query latency on the development machine.

Complete when the runner can identify a promising corridor or hill, inspect why
it is highlighted, and understand whether it is accessible from their start.

## M7 — Assemble a run with guidance

Select a destination or intermediate waypoints and request a return. Display
distance, ascent/descent, gradient profile, surfaces, and repeated distance as
the route changes. Offer understandable alternatives such as shorter, flatter,
or less retracing. Let the runner retain a selected section, avoid a place, and
regenerate the remaining route. Keep selected sections and waypoint order as
explicit constraints; explain when no feasible completion is found.

A route is a directed closed walk, allowing access stems and repeated sections.
If a graph bridge is the only connection to a chosen area, a return outing must
cross it twice. Include both traversals in distance and distinguish repetition
required by chosen places from avoidable reuse. Do not impose a universal 10%
repetition limit; that remains a historical baseline experiment setting.

Complete when a runner can build and revise a familiar outing, understand its
terrain and repetition, and identify unsuitable suggestions. Save a small set
of accepted/rejected examples to guide later scoring decisions.

## M8 — Suggest complete runs

Extend the general-graph baseline with distributed intermediate anchors and
alternative paths. Generate a bounded candidate set, evaluate whole routes,
and retain a few geographically different alternatives. Check actual distance,
legal traversal, required places/sections, terrain, turns, and repeated distance.
Use physical-length overlap rather than segment counts to compare alternatives.

Accept guidance such as a target distance, flatter terrain, a preferred park,
a hill to include, or an area to avoid. Explain tradeoffs instead of claiming a
global optimum. Surface and environment preferences follow evidence from M7.
Local path replacement and caching are later improvements driven by measured
quality or latency failures.

Optional loop suggestions may overlap and may come from accepted generated
routes or saved favourites. Selecting one calculates real access and return
paths and includes them in totals. Complete coverage is not a requirement.

Complete when suggestions satisfy their stated constraints, differ meaningfully,
and receive useful local-runner feedback on the saved evaluation examples.

## Cleanup and compatibility

The archived implementation is available at commit
`99141157453d0363889957821ecc921313ddcb86`; see
[recovery instructions](archive/README.md). Cycle-specific modules, plots, tests,
CLI commands, and topology settings are removed from the active codebase.

Routing experiment schema 2 removes the topology configuration. Existing prepared
graphs carry the old configuration digest and must be rebuilt with `paths prepare`
before running `routes baseline`. Reuse the cached OSM extract; downloading it
again is unnecessary. Ignored local cycle artifacts are left untouched and are
not consumed by the current commands. Terrain-region products are unaffected.

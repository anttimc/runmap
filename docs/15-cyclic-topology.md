# 15. Cyclic topology and half-edge faces

## Purpose

Step 4 prepares the graph structures needed to compare face-based loop search
with the diverse-path baseline. Connectivity topology is calculated across the
whole runnable experiment graph. Half-edge face enumeration is initially
limited to a 2.5 km × 2.5 km neighborhood around Ruusutorpan koulu:
`E 377000–379500`, `N 6676500–6679000` in EPSG:3067.

The directed routing graph remains authoritative for legal travel. The
undirected topology and half-edge embedding are supplemental indexes over its
physical segments.

## Connectivity topology

The topology preparation computes:

- **bridges:** segments whose removal disconnects their undirected component;
- **2-core:** the graph remaining after repeatedly pruning vertices with degree
  below two;
- **articulation points:** vertices whose removal separates a component;
- **biconnected blocks:** maximal edge groups without an internal articulation;
- **cyclic components:** connected cycle-bearing regions after bridges are
  removed from the 2-core.

The algorithms operate on accessible physical segments after configured route
boundaries and explicitly denied barrier nodes are applied. Parallel edges and
closed OSM ways are retained as valid two-edge and self-loop cycles.

This decomposition gives bridges a direct routing interpretation: a closed
route can use one only by traversing it again. The degree-one paved segment at
the school start is confirmed as a bridge outside the 2-core, matching the
access stem reported by the diverse-path baseline.

## Half-edge embedding

The face experiment first clips to its test bounds. Bridge, tunnel, covered,
and nonzero-layer segments are excluded from the planar face walk because a
two-dimensional rotation system cannot represent which path passes above the
other. They are not discarded: connected structural segments are retained as
explicit connector corridors. A corridor includes the shortest locally pruned
approach from each open end to a face, up to 500 m, and records every planar
face it touches. This lets later route search move between face regions through
an underpass or bridge without pretending that the crossing splits a face.

After removing structural segments, the local 2-core and bridges are
recomputed. This prevents clipping artifacts and ordinary dead ends from
becoming spurs in face walks.

Each remaining physical segment has two geometric half-edges. Outgoing
half-edges are ordered by their initial direction at a vertex. Face traversal
takes the preceding half-edge around the destination vertex, keeping the
bounded region consistently on one side. Positive-area closed walks become
bounded faces; the outer face and degenerate walks are rejected. Closed ways
and curved two-edge lenses are supported even when they contain fewer than
three distinct graph vertices.

Adjacent faces record their shared physical segment. Selecting faces combines
their segment sets by symmetric difference, so a shared segment cancels. A
synthetic pair of adjacent squares verifies that the two four-edge faces join
into one six-edge outer boundary.

## Command and outputs

```sh
PYTHONPATH=src .venv/bin/python -m runmap.cli routes topology \
  leppavaara-flat-10k
```

Ignored local outputs are written under
`data/paths/prepared/leppavaara-flat-10k/topology/`:

- `topology.json`: classifications, components, blocks, faces, and adjacency;
- `topology-segments.geojson`: bridge, 2-core, cyclic-component, face-embedding,
  and connector attributes;
- `faces.geojson`: bounded face polygons plus grade-separated connector lines.

## First measured result

| Measurement | Result |
| --- | ---: |
| Eligible physical segments | 67,892 |
| Bridges | 17,120 |
| Articulation vertices | 13,821 |
| 2-core segments / vertices | 51,216 / 37,728 |
| Biconnected blocks | 17,402 |
| Cyclic biconnected blocks | 282 |
| Cyclic components | 258 |
| Largest cyclic component | 49,259 segments |
| Face-test physical segments | 7,145 |
| Half-edges | 14,290 |
| Simple bounded faces | 2,003 |
| Adjacent face pairs | 5,832 |
| Grade-separated connector corridors | 92 |
| Connector segments | 346 |
| Connectors touching at least two faces | 58 |

Within the face test, 171 grade-separated segments were omitted from the
planar walk and retained through connector corridors; 2,107 ordinary segments
were removed by the local core/bridge cleanup. All half-edge walks closed.
Eleven closed walks were non-simple, while 52 were outer or degenerate.
Accepted face area ranges from about 1.9 m² to 136,352 m², with a median near
509 m². Very
small faces are retained for measurement; step 5 can decide whether they
should be filtered, merged, or ignored during loop search.

## Evaluation and limitations

Synthetic graphs verify bridges, articulation points, 2-core membership,
self-loop blocks, planar face direction, face adjacency, and shared-edge
cancellation. A plot of the real test neighborhood shows coherent block and
path-island faces. Grade-separated gaps remain absent from the polygons, as
required by the planar embedding, but their traversable paths are now visible
as connector lines and linked back to the adjacent faces.

This does not prove that every accepted OSM face is a useful running primitive.
Tiny roundabouts, duplicate geometry, and large irregular open-space faces
still require search-time policy. Some long, continuously
tagged tunnel networks form correspondingly long connector corridors; route
search should use their actual segments and face attachments rather than treat
the whole corridor as one indivisible move. Directed traversability must also
be checked after geometric faces are selected.

Step 4 is complete. Step 5 can seed connected face sets, modify them through
face adjacency, extract symmetric-difference boundaries, and compare the
resulting loops with the diverse-path baseline.

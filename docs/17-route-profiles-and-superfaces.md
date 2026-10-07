# 17. Route profiles, superfaces, and route shape

## Purpose

The first step-6 slices remove unsuitable route choices before face search and
prefer simpler route shapes.
An edge forbidden by a route profile should not remain a separator that creates
two artificial face choices. If it separates two bounded faces, those faces
are dissolved into one superface.

The first `street-shoes` profile rejects:

- `highway=track`;
- known loose or unpaved surfaces, including gravel, compacted, ground, dirt,
  earth, grass, mud, sand, and woodchips; and
- `highway=path` or `track` when surface is unknown.

The rules describe surface suitability, not environment. A paved forest path
remains usable. Identifying forest location itself will require land-cover
polygons in the OSM acquisition and can feed the same classifier later.

## Topology transformation

The base half-edge faces remain unchanged and reusable. For a profile:

1. classify all physical graph segments;
2. union adjacent faces whose shared segment is forbidden;
3. form each superface boundary by symmetric difference of its member faces;
4. reject a superface if a forbidden segment remains exposed on its outer
   boundary; and
5. rebuild adjacency between the remaining superfaces.

Shortest access and fallback return searches receive the same forbidden
segment set, so an unsuitable edge cannot re-enter outside the face boundary.
Each superface retains its base-face membership and can be expanded back to the
original topology for explanation and rendering.

## Stair and turn policies

Stairs are independent of surface suitability. The search supports three
modes intended for an eventual UI toggle:

- `avoid` adds `highway=steps` to the effective edge profile, dissolves faces
  across shared stair edges, and blocks stairs in access and return searches;
- `penalize` leaves stairs available but adds a configurable score per metre;
- `allow` adds no stair-specific restriction or score.

`avoid` is the current street-shoe default.

The turn policy distinguishes unavoidable road bends from route-level
reversals. By default, a boundary with a turn of 170 degrees or more is
infeasible. This is a near-U-turn constraint, not a prohibition on turning at
normal street junctions. Among otherwise feasible routes, the shape score
prefers fewer concave turns, a smaller accumulated turn penalty, and lower
isoperimetric compactness. These are practical proxies for a smooth,
convex-looking route; they do not assert that a road-network loop is a
mathematically convex polygon.

## Shape-aware growth

The initial search committed to one locally best face at every iteration and
only used the detailed shape score after producing a complete candidate. The
refined search retains a beam of three partial regions. For each beam state it:

1. forms connected frontier chunks of one, two, or three faces;
2. applies each chunk with shared-edge cancellation;
3. rejects proposals whose resulting boundary is not one simple cycle;
4. measures that projected boundary's length, concavities, accumulated turns,
   compactness, and near reversals; and
5. retains the three best distinct regions for the next expansion.

The growth rank combines target-length error with the same shape components
used for final selection. Near reversals receive an additional strong—but not
infinite—growth penalty because a later chunk can fill the indentation that
causes one. Deterministic jitter and twelve sampled proposals per chunk size
preserve alternative search trajectories without enumerating every possible
multi-face set.

## Validation

A synthetic pair of adjacent squares verifies that forbidding their shared
ground-surface edge produces one superface with a six-edge outer boundary. The
profiled face search then generates the same legal outer loop without using the
forbidden separator.

For the Leppävaara/Kilo test topology:

| Measurement | Result |
| --- | ---: |
| Base faces | 2,106 |
| Usable street-shoe superfaces | 1,186 |
| Superfaces with exposed forbidden boundaries | 91 |
| Dissolved separators | 1,662 |
| Forbidden segments in the full prepared graph | 17,362 |
| Evaluated face-region candidates | 250 |
| Feasible candidates | 114 |
| Expanded beam states | 4,011 |
| Proposed connected face chunks | 142,926 |
| Runtime | 40.0 s |

The added stair rule accounts for the difference from the surface-only
topology. With shape-aware growth, all five selected loops are feasible. They
range from 9.53 to 9.65 km, with 107.8–118.0 m ascent and 4.5–5.4% repeated
distance. They contain no stairs and no boundary turn at or above 170 degrees.
Each selected route used chunks of three faces, and 15–19 of its growth steps
added more than one face.

## Findings and next refinement

The simplification substantially reduces the topology, so profile-specific
superfaces are worth retaining. The stair constraint fixes the concrete 96 m
stair failure in the earlier best candidate. Shape-aware growth reduces the
selected boundaries from 108–112 concave turns to 71–73, approximately one
third, and increases feasible candidates from 4 to 114.

The improvement costs time and changes other objectives: runtime rises from
about 18 to 40 seconds, repeated distance rises from 1.9–2.7% to 4.5–5.4%, and
ascent generally increases. The routes remain visibly detailed because the
search still follows exact face boundaries. This establishes that construction
must be shape-aware, while also motivating a later local simplification pass
and explicit multi-objective tuning.

The next step-6 slices should add:

- local boundary simplification and explicit multi-objective tuning;
- optional forest/wood land-cover overlays;
- profile presets rather than one embedded experiment profile; and
- a directed closed-walk representation supporting boundary collapse and
  controlled out-and-back edges.

The next architectural iteration moves cycle discovery into preprocessing and
combines reusable good cycles at request time. See
[cycle-catalog route search](18-cycle-catalog-route-search.md).

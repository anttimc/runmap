# 21. Continuous cycle refinement

## Status and purpose

This is the active implementation plan for environment-first cycle discovery.
It supersedes plans 18–20, which generated overlapping standalone loops in
fixed length bands and attempted composition afterward. Those documents and
their prototype remain as historical evidence, but their unfinished roadmap is
not active.

The replacement builds a rooted, multiresolution partition of the runnable
face topology. It starts with very small valid regions, repairs poor shapes,
connects non-overlapping microcycles to the starting point through their
adjacency graph, and creates longer loops by merging adjacent regions. Splits
or merge rollback refine poor decisions.

The first study area remains Leppävaara and Kilo, with Ruusutorpan koulu as the
start and the maintained mixed-surface profile as the initial route profile.

## Core invariants

The implementation should preserve these properties rather than recover them
through final scoring:

1. Every stored region boundary is one simple cycle with at least one legal
   directed traversal.
2. Active peer regions have disjoint atomic-face sets.
3. Every non-root active region has a valid shared-boundary connection to the
   rooted region structure.
4. A merge is the face union of two disjoint adjacent regions; their shared
   boundary becomes internal and is removed.
5. Parent and child regions may contain the same faces, but they are never
   simultaneously active. An active resolution is an antichain of the merge
   tree.
6. Profile, stair, indoor-route, direction, and turn constraints apply at
   every level.
7. Every result records deterministic provenance: atomic members, parent or
   children, merge interface, metrics, and rejection reasons.

The non-overlap rule therefore applies to siblings or active peers. It cannot
apply between a composed parent and its children because the parent is their
union.

## Topology and data model

### Atomic regions

Use the profile-filtered face topology as the substrate. Forbidden shared
segments create must-link components: the base faces on either side cannot be
separate runnable cycles because that segment would become part of their
boundary. The resulting profile superfaces are indivisible units for the
first implementation. Normalize enclosed holes by absorbing whole enclosed
profile superfaces. The resulting atomic envelope keeps its source profile
membership and hole-fill provenance. An absorbed superface is no longer an
active peer.

Each atomic region records:

- member base-face IDs;
- boundary segments and ordered half-edges;
- neighboring atomic regions and shared runnable segments;
- area, perimeter, compactness, convexity, and turn metrics;
- legal direction alternatives and profile rejection reasons; and
- outward and return reachability from the supplied start.

Do not silently discard an atomic region merely because its shape is poor. It
may be required to connect otherwise useful regions. Classify it as accepted,
repair-needed, connector-only, or invalid.

### Region hierarchy

Represent every accepted microcycle or merged envelope as a region node:

```text
id
level
atomic_region_ids
boundary_segment_ids
legal_orientations
children
parent
root_id
merge_interface
route_metrics
shape_metrics
status and rejection reasons
```

The first version uses a binary merge tree. Alternative parents would turn it
into a DAG and complicate the non-overlap guarantee, so alternatives are
deferred until the tree has been evaluated.

Introduce a virtual home node. It connects to one or more root microcycles via
validated access attachments. This allows several geographic roots while
keeping one rooted catalog.

## Shape and composition policy

### Hard validation

At every level require:

- one connected degree-two boundary without holes;
- a legal directed orientation;
- no forbidden surface, indoor edge, or disallowed stair;
- no turn at or above the configured near-reversal threshold;
- no shared atomic faces between active peers; and
- for a merge, a non-empty runnable shared boundary and a valid union cycle.

Very concave regions cannot be accepted as standalone microcycles: they must
be repaired by a merge, marked connector-only, or rejected. Use conservative
configurable convexity floors for that classification. Compactness remains a
diagnostic and soft merge-ordering metric, without an acceptance floor. Keep
convexity as a soft ordering criterion above its floor. Connector-only
regions may fall below them because hard shape thresholds can otherwise
disconnect the structure.

### Merge measurements

For adjacent regions `A` and `B`, with shared boundary length `S`:

```text
P(A union B) = P(A) + P(B) - 2 S
marginal_length(B | A) = P(A union B) - P(A)
```

Record the shared interface, marginal length, newly covered runnable boundary,
shape change, splice turns, and terrain change. Prefer a lexicographic merge
ordering initially:

1. hard validity;
2. preservation of rooted connectivity;
3. repair of an invalid or poor microregion;
4. progress toward the next useful perimeter scale;
5. new coverage and useful marginal length;
6. compactness, convexity, turns, and boundary complexity; and
7. terrain characteristics such as ascent.

This avoids hiding a disconnected or redundant merge behind a weighted scalar
score. Record every comparison field so the ordering can later be changed
without reconstructing topology.

## Implementation stages

### Stage 1: atomic-region inventory

Replace the current length-band generator input with an explicit atomic-region
model built from profile superfaces.

- Verify boundary and dual incidences.
- Classify each region as accepted, repair-needed, connector-only, or invalid.
- Build the runnable shared-interface adjacency index.
- Persist the inventory and a rejection summary.
- Render every atomic boundary over the stronger base map with equal metric
  axes, using separate colors for each classification.

Evaluation gate: every retained atomic face belongs to exactly one atomic
region, shared interfaces agree in both directions, and the visualization
explains all gaps.

### Stage 2: disjoint microcycle partition

Create a partition of accepted microcycles without using a requested route
length.

- Accept atomic regions that already pass the initial shape policy.
- Put slivers and poor concave regions into a deterministic repair queue.
- Repair a region by merging it with the adjacent region that gives the best
  valid shape improvement while preserving partition disjointness.
- Mark necessary regions that cannot become good loops as connector-only.
- Preserve the atomic membership and repair sequence of every microcycle.

The first implementation should merge only; it need not perform an arbitrary
split yet. A merge can be undone because its children remain in the tree.

Evaluation gate: active microcycles are face-disjoint, every usable atomic
region is accepted or has an explicit reason, and recognizable local blocks
are not swallowed by unrelated large regions.

### Stage 3: rooted adjacency

Root the microcycle partition at Ruusutorpan koulu.

- Add directed outward and return distance indexes.
- Select several access-near root microcycles with geographic spread.
- Reconstruct actual outward and return paths for roots.
- Normalize first and last intersections with the root boundary.
- Validate attachment turns, repeated traversal, and access-cycle overlap.
- Traverse the microcycle adjacency graph breadth-first from the virtual home
  node and classify unreachable or disconnected subgraphs.

Evaluation gate: every production microcycle has a chain of valid composition
interfaces to a root, and every root has a fully validated home attachment.

### Stage 4: deterministic merge hierarchy

Build longer cycles by binary agglomerative merging.

- Insert valid adjacent pairs into a deterministic priority queue.
- Pop the best merge, revalidate the union boundary and directions, and create
  a parent node without deleting its children.
- Invalidate and recompute queue entries affected by the merge.
- Emit active antichain snapshots around useful perimeter scales rather than
  forcing every module into an exact 1 km band.
- Stop at explicit merge, perimeter, shape, or geographic-domain limits.

Store snapshots separately from the hierarchy. A snapshot chooses mutually
non-ancestral nodes whose atomic face sets remain disjoint.

Evaluation gate: every parent reproduces exactly from its two children, its
perimeter identity holds within numeric tolerance, and the 1–10 km snapshots
remain rooted and non-overlapping.

### Stage 5: rollback and splitting

Add refinement only after merge behavior is visible.

1. First support rollback: undo a poor merge and try the next admissible
   neighbor pairing.
2. Add narrow-neck detection using weak or short dual interfaces.
3. If needed, introduce a bounded dual-graph cut that partitions one region
   into two connected atomic sets.
4. Require both split children and their complement relationships to pass the
   same simple-cycle validation.

Prefer rollback over arbitrary splitting in the first iteration. It is easier
to audit and retains deterministic provenance.

Evaluation gate: refinement improves a stated metric or restores useful route
choice; it must not merely increase the number of catalog entries.

### Stage 6: request-time route extraction

Use the hierarchy only after its structure is stable.

- Choose rooted hierarchy nodes or one active snapshot close to the requested
  total length and terrain goal.
- Include access and return paths in total distance and repetition.
- Expand the selected parent nodes to a directed road route.
- Apply final distance, ascent, turn, stair, and repetition validation.

The route request selects among already valid compositions. It should not need
to repair topology or invent joins at this stage.

## Outputs and diagnostics

Introduce a new schema rather than extending the overlapping schema-2 catalog
indefinitely. Expected prepared outputs are:

- `atomic-regions.json` and `.geojson`;
- `microcycle-partition.json` and `.geojson`;
- `cycle-hierarchy.json`;
- `cycle-hierarchy-snapshots.geojson`; and
- metric diagnostic images for the atomic, microcycle, rooted, and merged
  stages.

Every stage reports accepted, repaired, connector-only, invalid, merged,
rolled-back, and unreachable counts. Timings should separate topology,
validation, metric calculation, access reconstruction, and rendering.

The current `cycle-catalog.json` schema 2, fixed length bands, and rooted
pairwise composition output are deprecated experimental artifacts. They may be
used for comparison while stages 1–4 are built, but new code should not depend
on them as a stable interface. Once the hierarchy reproduces their useful
loops, replace the `routes catalog` implementation rather than maintaining two
production catalog formats.

## Testing and evaluation

### Stage 2 implementation and first evaluation

Stage 2 is implemented by `runmap.routing.microcycles`. It builds a fresh
atomic inventory using the current profile, then repairs the active partition:

```sh
MPLCONFIGDIR=artifacts/mpl-cache PYTHONPATH=src .venv/bin/python -m runmap.cli routes microcycles leppavaara-flat-10k
```

The command writes `microcycle-partition.json`, `.geojson`, and
`microcycle-partition-summary.json` under
`data/routes/prepared/leppavaara-flat-10k/`. The JSON includes all atomic leaves,
binary repair parents, active IDs, exact atomic/base-face ownership maps,
shared interfaces, repair sequences, and every attempted neighbor comparison.
Parents preserve their children as inactive nodes. The GeoJSON contains only
active regions, including connector-only and invalid diagnostics, and records
their atomic membership and repair provenance. The source atomic inventory is
identified by a deterministic SHA-256 excluding runtime timings.

Diagnostic images are:

- `artifacts/leppavaara-flat-10k-microcycle-partition.png`: active classifications;
- `artifacts/leppavaara-flat-10k-microcycle-partition-repairs.png`: before/after
  maps and repaired envelopes, labeled with merge IDs. Orange dotted lines show
  original atomic boundaries; blue lines show the repaired envelopes.

`--plot PATH` changes the classification image path; the comparison image uses
the same stem with `-repairs`. Both render over the stronger path map with equal
EPSG:3067 axes. Gray paths can be internal to a region, including a shared
boundary removed by a merge, or outside retained bounded faces.

The deterministic queue processes low-convexity regions first, followed by
standalone-policy failures such as fully unknown boundaries. It evaluates
adjacent active peers and requires an exact face union, a nonempty runnable
shared boundary, one simple outer cycle without holes, a legal direction,
profile compliance, and valid junction turns. The atomic and microcycle stages
share the boundary evaluator. Surface policy and shape metrics are recomputed
on the resulting boundary, so an internal known-surface edge cannot qualify an
otherwise unknown outer loop.

A merge must resolve the target's standalone-policy deficit or reduce its
convexity deficit. Consuming an accepted neighbor is allowed only when the
result is accepted. Candidate ordering prefers completed repair, lower
remaining deficits, preservation of accepted neighbors, shape improvement,
fewer atomic members, and smaller area, followed by the remaining shape/route
metrics and deterministic IDs. All comparison fields and rejection reasons
are stored. Compactness remains a soft measurement; thin regions that pass
the current convexity/profile rules are accepted without a sliver threshold.

The default locality limit is four atomic regions per microcycle. Override
`topology.microcycle_partition.maximum_atomic_regions_per_microcycle` to change
it. Optional `maximum_merges` bounds total repair work; the default is the
number of input atoms minus one. `minimum_convexity_improvement` defaults to
0.000001. No requested length, length band, or perimeter target is used.
The queue repeats after successful merges so deferred regions can use changed
neighbors. Unrepaired hard-valid regions finish connector-only with their
policy reasons and an explicit outcome, including no runnable neighbor,
no valid improving neighbor, or a merge-budget limit.

The first Leppävaara result is:

| Classification | Atomic inventory | Active microcycle partition |
| --- | ---: | ---: |
| accepted | 1,716 | 1,716 |
| repair-needed | 1 | 0 |
| connector-only | 130 | 43 |
| invalid | 49 | 49 |
| total | 1,896 | 1,808 |

There are 88 repair merges and 73 final repaired envelopes, incorporating 161
atoms: 73 accepted atoms, 87 connector-only atoms, and the one poor-shape atom.
The accepted count stays constant because each repaired envelope replaces one
accepted neighbor while extending coverage over formerly excluded regions.
Sixty-two repaired envelopes contain two atoms, seven contain three, and four
contain four. The longest repaired perimeter is 1,042.379 m. The poor-shape
repair (`microcycle-merge-1`) reaches a convexity ratio of 0.952347.

The remaining 43 connector-only regions consist of 17 with no runnable
neighbor and 26 with no admissible improving merge under the current rules.
Of 367 neighbor comparisons, rejected reasons include six non-simple unions,
four accepted-neighbor regressions, and three atomic-limit failures; an attempt
can have multiple reasons. The 49 invalid atomic regions remain explicit
diagnostics. Stage 2 inherits the missing-segment and embedding limitations
reported in stage 1; it does not infer new road segments or fix the substrate.

The evaluation verifies that all 2,106 retained base faces and all 1,896 atoms
have exactly one active owner, active peers are disjoint, parent membership
and boundaries reproduce exactly from their children, area/perimeter
identities hold within rounding tolerance, and the 3,774 runnable interfaces
agree in both directions. Merge contraction preserves external adjacency.
All 1,984 hierarchy nodes are retained, and 15 active regions lack directed
round-trip reachability. Root attachments are still stage 3 work.

Visual inspection shows repairs concentrated in the northern and southwestern
areas, with individual local envelopes visible in the comparison image.
Further manual judgment about natural local blocks remains useful before
choosing larger hierarchy scales. Synthetic coverage includes best-neighbor
concavity repair, two-square unions, unknown/mixed surface policies, loss of a
known internal edge, holes, incompatible one-way union orientations, access
exclusions, ownership overlap, explicit limits, perimeter corruption, and
deterministic reconstruction after serialization. The relevant 78 tests pass.

### Stage 1 implementation and first evaluation

Stage 1 is implemented by `runmap.routing.atomic_regions`. Run it on the
prepared graph and topology with the experiment's face-search profile:

```sh
MPLCONFIGDIR=artifacts/mpl-cache PYTHONPATH=src .venv/bin/python -m runmap.cli routes atomic leppavaara-flat-10k
```

The renderer requires the `experiments` extra (`matplotlib`). It uses the
file-only Agg backend. `--plot PATH` changes the diagnostic image destination;
the default is `artifacts/leppavaara-flat-10k-atomic-regions.png`. The inventory,
GeoJSON, and rejection summary are written under
`data/routes/prepared/leppavaara-flat-10k/`.

The current Leppävaara evaluation contains all 2,106 retained base faces in
1,896 atomic regions after profile filtering, with exactly one owner per face:

| Classification | Count |
| --- | ---: |
| accepted | 1,716 |
| repair-needed | 1 |
| connector-only | 130 |
| invalid | 49 |

The inventory verifies source boundary/half-edge incidences, the dual's exact
agreement with shared boundaries, atomic membership, and both directions of
3,966 runnable shared interfaces. Fifteen regions lack a boundary vertex
with both outward and return reachability; reachability does not remove them.
Invalid regions include 48 with forbidden outer boundaries and one whose
boundary is not one cycle. Every invalid boundary
is retained in the diagnostic GeoJSON. Source topology exclusions are recorded
separately, since atomic ownership applies to retained bounded faces.

Enclosed-hole filling is enabled by default; set
`topology.atomic_regions.fill_enclosed_holes` to `false` for the raw profile
inventory. Boundary components must be simple rings, with one outer ring
containing the others. Whole profile regions within the holes are absorbed
only when the symmetric difference of the resulting base-face boundaries
reproduces exactly the outer ring. Face ownership, area, centroid, metrics,
directions, and adjacency are rebuilt. Forbidden outer edges remain invalid.

Before allowing ground and dirt, five holes were filled by absorbing nine
profile regions. The largest
southwest region (`profile-face-131`) absorbs the 53.249 m² inner face and
becomes an accepted 1,950.652 m cycle enclosing 208,491.249 m². The prepared
graph reports that inner loop as reachable under the profile. Hole filling
applies to both reachable and unreachable loops and records their
reachability; it selects the outer envelope for the active inventory.
Source membership is preserved in `base_face_to_profile_region`, and every
fill records absorbed faces and removed boundary segments. One region in that
evaluation (`profile-face-664`) remained invalid with an `incomplete-hole-coverage`
diagnostic because the retained faces do not reproduce a complete fill.

Before hole filling, the baseline inventory had 1,568 regions: 1,448 accepted,
39 repair-needed, five connector-only, and 76 invalid. Absorbing enclosed
regions reduces the number of active peers, and repaired envelopes change
which poor regions are adjacency articulations.
With hole filling but the original shape floors, 1,443 regions were accepted,
42 needed repair, and two were connector-only. Removing the compactness floor
and relaxing convexity to 0.25 accepts 43 more regions, leaving one region
below the convexity floor. The 72 hard-invalid regions are unaffected.

Allowing `surface=ground` and `surface=dirt` created 119 additional atomic
regions and 120 additional
accepted regions: allowed separators retain smaller peers instead of forcing
must-link dissolution. Forbidden outer boundary segments drop from 179 to 142,
and regions with forbidden boundaries drop from 71 to 70. The remaining
forbidden segments are 106 paths without surface tags, 30 stair segments,
three grass segments, and one each of unknown-surface track, mud, and unpaved.
That inventory filled four holes by absorbing eight profile regions;
`profile-face-720` retained the incomplete-hole-coverage diagnostic.

The current profile also allows missing surface tags on paths and tracks.
Compared with the ground/dirt profile, accepted regions increase from 1,606
to 1,962, and forbidden-boundary regions decrease from 70 to 33. Forbidden
boundary segments decrease from 142 to 35: 30 stairs, three grass segments,
one unpaved segment, and one mud segment. No surface value is inferred for
untagged paths; the profile simply allows them. That inventory filled
one hole by absorbing one profile region. `profile-face-843` retained the
incomplete-hole-coverage diagnostic. Region IDs are deterministic within a
profile, but dissolution changes can renumber them.

Standalone loops now require at least one boundary segment with a known
surface, through `edge_profile.require_known_surface_on_boundary: true`.
A missing surface tag or the explicit value `unknown` counts as unknown.
Mixed known/unknown boundaries remain eligible; internal known edges do not
qualify an unknown outer boundary. This is a region policy, so unknown edges
remain usable for directed access and interfaces and are not dissolved.
Forbidden surfaces, stairs, directions, and turn constraints still apply.

Fully unknown hard-valid regions become connector-only with
`standalone_rejection_reasons: ["all-unknown-surface"]`. Their legal directions
and adjacency remain available so future merges with known-surface neighbors
can create acceptable mixed loops. Surface distances, segment IDs, and the
unknown distance fraction are stored as `surface_metrics` in JSON and GeoJSON;
these measurements must be recalculated on the resulting boundary after a
merge. Before tightening access, the rule moved 154 of the previously accepted
loops to connector-only, leaving 1,808 accepted. Two further fully unknown
regions already failed hard checks, making the `all-unknown-surface` count 156.
Surface tags alone do
not establish whether a loop is in forest.

The current profile also sets `edge_profile.forbidden_access_values` to
`no`, `private`, `destination`, `customers`, `permit`, and `delivery`.
Pedestrian `foot` restrictions take precedence over generic `access`.
Explicit `foot=yes`, `designated`, `permissive`, or `official` overrides a
generic access restriction. Restrictions on tagged nodes and gates also
exclude incident segments. This shared classification filters boundaries,
must-link separators, runnable interfaces, and directed outward/return
distance indexes. Private land enclosed by a legal outer boundary remains
eligible; missing property access tags require area data or manual exclusions.

With tightened access, the inventory has 1,716 accepted regions, 130
connector-only regions, one repair-needed region, and 49 invalid regions.
No usable atomic boundary contains a profile-forbidden segment. The
`all-unknown-surface` count is 133, including three hard-invalid regions.
No holes are filled under this profile; `profile-face-817` retains the
incomplete-hole-coverage diagnostic. Changes in these counts reflect both
boundary exclusions and dissolution of restricted internal separators.

Compactness and isoperimetric quotient are diagnostic metrics and do not
affect acceptance or repair classification. The default convexity floor is
0.25, relaxed from the initial 0.55 to retain small concave patches whose
combinations can be closer to convex. Override it through
`topology.atomic_regions.minimum_convexity_ratio`. The configured
face-search near-reversal threshold is a hard boundary constraint. Geometry
metrics use complete segment geometry in EPSG:3067 and turns use segment
tangents at junctions. Indoor edges, forbidden surfaces, stairs, blocked
vertices, route boundaries, and legal arc directions are checked independently
of route length.

A poor but hard-valid region is provisionally connector-only when it is an
articulation in the usable atomic adjacency graph. Other poor regions remain
repair-needed for stage 2. This is a structural classification; it does not
claim a validated home attachment or a completed repair search.

The diagnostic image has an overview and one panel per classification, with a
strong gray base map and equal metric axes. Visual inspection confirms that
poor and invalid regions remain visible, including the large forbidden regions
on the western side. Stage 2 still needs to determine which repair merges make
natural local microcycles. The deprecated catalog is available for comparison;
the atomic command and schema do not depend on its length bands or pool.

Synthetic coverage includes symmetric interfaces and serialization,
pedestrian access precedence, restricted outer edges and internal separators,
restricted gates, and outward/return access stems,
standalone unknown-surface exclusion with preserved connectivity, mixed
boundaries, known internal edges, forbidden surfaces on mixed boundaries,
must-link dissolution, forbidden outer boundaries, indoor separators, poor
shapes, connector articulations, reachable and unreachable hole filling,
incomplete hole coverage, forbidden outer edges after filling, holes with
filling disabled, geometric crossings, near reversals,
closed OSM ways, inconsistent one-way directions, disconnected home vertices,
and corrupt source incidences.

Synthetic tests should cover:

- two adjacent squares merging into one rectangle;
- a sliver repaired by its best neighbor;
- a concave union rejected or rolled back;
- a merge that creates a hole;
- siblings with accidental face overlap;
- an articulation or connector-only atomic region;
- a one-way root attachment without a return path;
- several root components attached to the virtual home node; and
- deterministic hierarchy reconstruction after serialization.

The first Leppävaara evaluation should answer:

- Are the microcycles recognizable non-overlapping local areas?
- Which regions require repair, and do their chosen merges look natural?
- Does rooted breadth-first expansion cover the places a runner would expect?
- How much perimeter and new coverage does each merge contribute?
- How should compactness guide merge ordering while preserving connectivity?
- Do hierarchy snapshots provide genuinely different route choices without
  redundant face overlap?

Manual visual acceptance remains part of this stage. Exact MILP optimization,
global alternative-DAG selection, forest land-cover enrichment, and arbitrary
splitting are explicitly deferred until the deterministic hierarchy exposes a
specific need for them.

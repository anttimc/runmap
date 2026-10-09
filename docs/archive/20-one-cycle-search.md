# 20. Start-local one-cycle search

> **Archived 2026-10-09 — not an implementation plan.** Superseded by the
> [map-first plan](../22-map-first-running-plan.md). Status statements and commands
> below describe the historical experiment. Its cycle-specific code was removed;
> see [archive recovery instructions](README.md).

> **Status: superseded.** The exact one-cycle formulation and deterministic
> beam search are retained as reference material, not as the active catalog
> roadmap. The replacement plan builds a disjoint rooted region hierarchy; see
> [continuous cycle refinement](21-continuous-cycle-refinement.md).

## Purpose

Before composing a catalog into longer routes, make the generation of one good
cycle explicit and visually testable. The input is a starting point, a profiled
face topology, and optionally a desired circumference or length band. The
output is a small set of legal, shape-diverse cycles near the start.

This search is the inner algorithm of start-local catalog preprocessing.
Deterministic growth from reachable seeds emits into several length bands in
one run. A linear integer model provides a reference oracle on small bounded
instances; exact optimization is not a prerequisite for generating a catalog.

## Dual graph

The usable topology naturally defines a dual graph:

- each usable face or profile-specific superface is one dual vertex;
- two dual vertices are adjacent when their faces share a runnable boundary
  segment; and
- the shared segment labels the dual edge.

For optimization, first decompose the filtered planar graph into cyclic
biconnected blocks. Every simple cycle lies within one such block, so the exact
oracle does not need one global embedding spanning articulation points or
disconnected cyclic components. Recompute these blocks after applying the
active profile and removing grade-separated structural segments: deleting a
forbidden segment can split a previously computed block.

Build a complete embedding independently for each oracle block, including its
explicit outer face and both face incidences of every segment. A segment
belonging to only one *retained* bounded face does not necessarily border the
exterior: the current extractor discards non-simple walks, and profile
processing removes some superfaces. Resolve these missing incidences before
using cycle/bond duality as an exact oracle. Block-local decomposition should
eliminate articulation-induced non-simple face walks; any remaining non-simple
walk is an embedding defect or an exceptional case that must be retained for
incidence accounting but excluded from selectable cycle regions.

Surface and stair constraints are applied before constructing this graph.
When a forbidden shared segment is dissolved, the corresponding base faces
belong to one superface rather than remaining adjacent across an unusable edge.
This dual-edge contraction also applies to forbidden edges incident to the
outer face. Preserve base-face membership for area and provenance.

For a bounded solve within one block, fix excluded faces to zero while
preserving their dual connectivity to each other and the block's outer face.
Fixed-zero connected components may be contracted individually; disconnected
excluded regions must not all be identified with the exterior, since doing so
can hide holes.

The route start is snapped to the runnable road graph. Faces incident to the
snapped segment, or the nearest reachable faces when there is no incident
face, form the root set. A dual-graph breadth-first traversal assigns each face
a minimum layer number from these roots. Exclude the outer vertex from this
traversal: it is a connectivity device, not a shortcut between distant faces.
Layers count face crossings, not metres, and are used for diagnostics and
bounded solve domains rather than as the access budget.

Catalog seeds also include other reachable faces, ordered by runnable access
distance with stable face-ID ties and a bounded geographic spread. A catalog
cycle need not enclose a home-adjacent face. Use directed shortest-path distances
from home and from home on the reversed graph to index outward and return
access once per start/profile. Initially require a portal with both distances
within the configured one-way access budget. Record both distances; reconstruct
paths only for retained candidates and validate their attachment turns.

Bridges, tunnels, and other connector corridors that do not bound planar faces
need an explicit augmented-dual transition later. They must not be invented as
ordinary face adjacency or included in the block-local oracle. A final simple
cycle that uses such connectors belongs to a later hybrid formulation.

## Exact dual-cut formulation

The dual graph supports a more rigorous reference method than greedy region
growth. In a connected plane graph, a simple cycle in the primal graph
corresponds to a bond, or inclusion-minimal cut, in its dual. Equivalently, a
set of inside faces has one simple boundary when both the selected dual faces
and their complement containing the outer face are connected. This turns the
one-cycle problem into binary face selection.

Let `x_f` be 1 when bounded face `f` is inside the cycle and 0 otherwise. Fix
`x_outer = 0`. For every primal segment `e` separating dual faces `i` and `j`,
let `y_e` indicate that the segment belongs to the cycle. Only `x` needs binary
variables: continuous `y` in `[0, 1]` is forced to 0 or 1 by the exclusive
or relation `y_e = |x_i - x_j|` with linear inequalities:

```text
y_e >= x_i - x_j
y_e >= x_j - x_i
y_e <= x_i + x_j
y_e <= 2 - x_i - x_j
```

For an outer-face segment, use `y_e = x_f`. Segments with the same unordered
face pair share one XOR value and can be aggregated by total length, retaining
their original segments for access constraints and route validation. Segments
with the same face on both sides have `y_e = 0`. Area and circumference are:

```text
A = sum_f area_f * x_f
P = sum_e length_e * y_e
```

Require the selected faces to be connected to a chosen seed face,
and require the unselected faces to be connected to the outer face. These two
connectivity conditions initially use explicit single-commodity flows with
capacities bounded by the local face count. Defer callback-based connectivity
cuts until profiling justifies another backend. Under the
plane-graph assumptions, the resulting cut is a bond and its primal segments
form one cycle. The existing degree-two and directed-route validators should
still check the result because imported road topology can contain degenerate
or exceptional embeddings.

Use the precomputed outward and return distances to identify eligible portals.
A covering constraint requires at least one selected boundary segment incident
to an eligible portal. Validate both cycle orientations, near reversals, and
the access/return attachments after solving. A reconstructed outbound path must
attach at its first intersection with the cycle boundary, and a return path at
its last boundary intersection. Either normalize the nominal portal to those
effective attachment vertices or reject the connector. Measure access-cycle
overlap, outward-return overlap, and all resulting repeated traversal; overlap
is allowed only through the configured repetition policy and must not be hidden
by the portal abstraction.

If no legal orientation and access/return attachment combination exists after
checking every eligible effective portal, exclude that boundary with a no-good
constraint and re-solve. This external validation loop is distinct from solver
callbacks; report its solve count and time. Only a validated incumbent is a
runnable result, and optimality requires the valid incumbent to meet the bound
for the stated model.

There are two useful anchoring variants:

- **enclosing cycle:** fix a start-local root face to `x_root = 1`; the cycle
  surrounds that face and may use a short access connector from the exact
  snapped start; and
- **start-on-cycle:** split the snapped road segment if needed and constrain
  exactly two selected boundary segments to meet the start vertex.

These are optional modes. The initial oracle fixes one seed to simplify its
connectivity model; that seed may be any reachable face. Its optimum is scoped
to that seed, domain, and bucket. Multiple seed solves broaden coverage but do
not certify the unrestricted near-home problem unless their coverage is proven.

For target circumference interval `[L_min, L_max]`, impose

```text
L_min <= P <= L_max
```

and maximize `A`. This is an exact mixed-integer linear formulation of maximum
enclosed area within that length band. Use narrow perimeter buckets when
comparing substantially different circumferences, since maximizing area over a
wide band naturally favors its longer end. Alternatively, first minimize
absolute target-length error and then maximize area among equally close
solutions.

Keep compactness optimization linear initially. For a perimeter bucket `[l,u]`
with `l > 0` and a valid upper bound `U_A` on maximum area:

```text
Q <= 4 pi U_A / l^2
```

Compare this bound with the best validated `Q` and split only buckets that
could improve it. If area is solved optimally over the eligible cycles in a
bucket, the returned cycle achieves at least `(l/u)^2` times that bucket's
optimal `Q`: its area is no smaller and its perimeter is at most `u`, whereas
the compactness optimum has perimeter at least `l`. With a time limit, use
the actual incumbent and area bound instead of claiming this solved-bucket
guarantee. Convert the solver's minimization bound on `-A` to an upper bound on
`A` before using it. Bounds apply only to the same seed, domain, and constraints.

Start with a few domains and coarse buckets, refining adaptively rather than
solving every radius/bucket combination. Exact nonlinear or conic `Q`
optimization is deferred until these bounds demonstrate a practical need.
A time-limited solve must report its bound and gap, including when no valid
incumbent has yet been found.

Convex-hull ratio and detailed turn quality are also awkward to express
linearly. Initially compute them after solving, or use them as lexicographic
tie-breakers when enumerating near-optimal area solutions. The exact model's
main job is to certify the best area attainable under topology, length, and
runnability constraints.

Use CVXPY as the modeling layer. Boolean face variables, continuous boundary
variables, flow connectivity, and no-good constraints are easier to review than
manually assembled sparse matrices. The initial linear model should explicitly
select CVXPY's `SCIPY` backend, which uses the HiGHS MIP solver already bundled
through the project's SciPy dependency. CVXPY improves model clarity and allows
solver substitution; it does not itself make the solve faster.

Parameterize bucket bounds and reusable objective data where possible to avoid
repeated model compilation. Record compilation and solving time separately.
SciPy's documented `milp` interface has no MIP-start or lazy-cut callback
argument. A heuristic candidate can supply an area lower bound for the same
model, but is not a solver warm start through this interface. Parameter reuse
also does not imply reuse of a branch-and-bound tree. Change backends only if
measurements justify these capabilities.

Before adaptive compactness refinement depends on solver bounds, run a
capability check against the pinned CVXPY/backend versions. Verify and test
access to the incumbent objective, MIP dual bound, relative gap, and termination
status for both optimal and time-limited solves. CVXPY solver statistics are
backend-specific. If its `SCIPY` adapter does not preserve the required HiGHS
fields, evaluate CVXPY's direct `HIGHS` backend or use
`scipy.optimize.milp` for the reference oracle. Do not derive `U_A` from a
missing field or from an incumbent value.

The project now requires Python 3.11 or later so the implementation can use the
current CVXPY release line. Add `cvxpy>=1.9,<2` when the first optimization
implementation lands. Do not add the dependency before code uses it; at that
point, record the resolved CVXPY and solver versions in the tested inspection
environment.

## Search state and expansion

A state represents a connected face region rather than a partial road path:

```text
R          selected face IDs
B          current boundary segment set
A          enclosed face area
P          current boundary perimeter
F          adjacent unselected faces
d          greatest dual layer reached
```

For an initial face, `B` is that face's boundary. Adding an adjacent face
updates the boundary by symmetric difference:

```text
B' = B XOR boundary(added face)
A' = A + area(added face)
P' = P + perimeter(added face) - 2 * length(B intersect boundary(added face))
```

Shared internal segments disappear and new external segments appear. Cache
face perimeters and segment lengths. Deduplicate selected-face signatures
before boundary ordering; cache measured boundaries across seeds and bands.
Initially retain only connected regions with one connected degree-two boundary
without holes. This structural restriction is itself heuristic and must be
included in comparisons with the oracle.

Intermediate states need not have a legal directed traversal or satisfy the
near-reversal limit: later additions can remove offending boundary edges.
Apply the hard route constraints to emitted candidates. Compute convex hulls
and full route metrics only for retained states or candidate outputs, not
every proposal. Profile filters remain active throughout construction.

The first heuristic implementation should add exactly one adjacent face at a
time. Then perform a bounded local refinement with face additions, removals,
and swaps, retaining a simple boundary and preventing repeated states. Compare
against the existing two/three-face chunk generator: doc 17 found three-face
chunks in every selected route. Chunks are a measured coverage option, not
restricted to the next dual layer; useful repairs can involve earlier layers.

## Deterministic multiband growth heuristic

For diagnostics, define a state's dual radius as:

```text
radius(R) = max(layer(face) for face in R)
```

The first implementation uses bounded rounds of growth per seed, with seeds
processed in access-distance order. It does not exhaust every smaller-radius
subset before allowing outward progress. The procedure is:

1. Initialize a bounded set of reachable seeds with geographic spread.
2. Propose adjacent single-face additions for retained states in each round.
3. Deduplicate, update area/perimeter, and check simple-boundary structure.
4. Validate and emit candidates into every matching catalog length band.
5. Keep a small deterministic beam per coarse perimeter bucket, ranking by
   cheap shape measures and stable IDs; cap the total states per seed as well.
6. Refine promising candidates with bounded add/remove/swap moves.
7. Stop at explicit expansion, face-count, domain, or time budgets and report
   the stopping reason. Deterministic expansion budgets are the comparison
   default; wall-clock limits can change the resulting candidate set.

The state space consists of connected face subsets and is exponential even
though face-layer assignment itself is linear. Start with this simple bounded
beam instead of a multidimensional Pareto frontier for every radius/bucket.
Add frontier diversity or further partitioning only when comparisons show
which useful candidates are being lost.

This pruning is heuristic. Apparent dominance between two partial regions is
not generally safe: their different frontier faces can permit very different
future cycles. The growth algorithm therefore cannot claim global
optimality once it drops a state. Its result should be compared with the exact
dual-cut solution on the same bounded face set, and its report should state how
many states were pruned.

Adding a face can either increase or decrease the boundary length because
several old boundary segments may become internal. A state therefore cannot
be discarded merely because its current circumference is slightly above a
target. Initial safe limits should be maximum dual layer, maximum selected
faces, and maximum search states; stronger length bounds require a justified
lower-bound calculation.

## Shape objective for one cycle

Let `A` be the enclosed area and `P` the cycle circumference. For a fixed
circumference, maximizing area expresses the desired preference for a broad,
smooth loop over a deeply indented one. Across different route lengths, use the
dimensionless isoperimetric quotient:

```text
Q = 4 pi A / P^2
```

`Q` is at most 1 for an ideal planar curve, with the circle as the optimum.
Larger values are better. The current implementation stores its reciprocal,
`P^2 / (4 pi A)`, as `compactness`; that value is at least 1 and smaller is
better. Either representation is valid, but output should expose one canonical
name and direction to avoid scoring mistakes.

Compactness discourages concavity but does not describe it completely. Also
measure the convexity ratio

```text
C = A / area(convex hull of the boundary)
```

where 1 means that the region has no geometric indentation. `C` alone cannot
distinguish a circle from a long thin rectangle, so `Q` and `C` are
complementary. Junction measures remain useful because road-network geometry
can have many small turns even when its large-scale envelope is compact.

### Hard constraints

- one simple closed boundary with a legal directed traversal;
- permitted access, surface, and stair policy;
- no forbidden near reversal;
- circumference inside the candidate collection tolerance; and
- a portal with legal outward and return access within the configured budget,
  including effective first/last boundary attachments, valid attachment turns,
  and repetition within the configured limit after path reconstruction.

### Optimization criteria

For cycles satisfying the hard constraints, record these quality dimensions:

- maximize isoperimetric quotient `Q`;
- maximize convexity ratio `C`;
- minimize concave and sharp junction turns;
- minimize unnecessary boundary complexity; and
- minimize access distance from the supplied start.

Catalog selection preserves geographic and terrain representatives as described
in plan 19; geometry-only Pareto pruning can otherwise erase hill-training
options even when ascent is recorded. For the initial growth beam, rank within
coarse perimeter buckets by `Q`, turn measures, and stable face IDs. Reserve
convexity and detailed access checks for survivors. Ranking and beam limits are
search controls, not safe dominance proofs or a complete route-quality model.

## Candidate validation and output

For every emitted candidate:

1. deduplicate the boundary signature and order its segments into a cycle;
2. test both orientations against directed arcs;
3. compute exact circumference, area, `Q`, `C`, and turn measures;
4. identify outward/return portals from cached distances, then reconstruct and
   normalize first/last boundary contacts, validate access paths and attachment
   turns, and measure connector/cycle repetition for retained candidates;
5. record selected faces, boundary segments, dual radius, solution method, and
   solver or growth provenance; and
6. retain legal orientation alternatives and their direction-specific metrics
   under the same geometric cycle ID rather than choosing only the flattest.

The diagnostic visualization should show the dual-face layers beneath the
candidate, the start and access connector, and the filled enclosed region. A
table beside it should report `P`, `A`, `Q`, `C`, concave turns, face count,
maximum layer, and access distance. This makes it possible to tell whether a
high numerical score corresponds to a loop a runner would actually recognize.

## Optional exact candidate enumeration

The same model can produce a candidate pool without relying on randomized
greedy variants:

1. Solve selected seed/domain/bucket instances for comparison with growth.
2. Record the optimum and solver gap.
3. Add a no-good constraint excluding the exact boundary just returned.
4. Add a minimum symmetric-difference or maximum shared-length constraint
   against accepted boundaries.
5. Re-solve while requiring area to remain within a configured tolerance of
   the optimum.
6. Expand selected domains or refine promising buckets within an explicit solve
   budget. Access distance and dual radius remain separate limits.

Because one side of each overlap comparison is already fixed, boundary
difference and shared length are linear expressions in `y_e`. This gives a
deterministic pool of `k`-best or diverse near-optimal candidates. Domain
selection defines where to solve, while integer optimization determines the
best cycle under that domain's seed and length constraints. An explicit
positive difference constraint already excludes the previous boundary; avoid
adding a redundant no-good constraint in that case.

Sequential overlap constraints are order-dependent, so they do not by
themselves define a globally optimal catalog. Initially use plan 19's greedy
coverage/diversity selector. Defer a second binary selection model until the
candidate pool and desired composition coverage are understood. It could impose
counts per length band, reject pairs above the overlap limit, and maximize
covered faces or segments with quality as a secondary objective. This gives an
optimal catalog relative to the enumerated candidate pool. It is only a global
catalog optimum if the pool contains every eligible cycle, which is unlikely
at large radii and must not be implied in reporting.

The exact method is likely to become expensive as the local face set grows.
Its initial role should therefore be a reference oracle on bounded radii, not a
premature promise that every production catalog will be solved to global
optimality. Deterministic growth remains the initial catalog generator; its
validated candidates also give lower bounds for matching area models.

## First implementation and evaluation

Current implementation status:

- profile filtering, cyclic-block recomputation, complete incidence recording,
  and block validation are implemented;
- directed outward/return indexing and access-ordered reachable seeds are
  implemented;
- one deterministic multiband traversal per seed and variant is implemented,
  with coarse perimeter buckets, early face-set deduplication, incremental area
  and perimeter, explicit prune counts, and physical-length overlap selection;
- candidate output now records both legal cycle orientations, access portals,
  isoperimetric quotient, reciprocal compactness, and convexity ratio; and
- local add/remove/swap refinement, reconstructed connector validation, and the
  exact MILP oracle are not yet implemented.

A separate first composition pass now chooses several access-near roots from
the 1 km compatibility graph and builds two- to four-module envelopes. Modules
must have disjoint face interiors and a shared runnable boundary; every union
is revalidated as a single directed cycle. This establishes explicit
root/parent/module provenance, but it does not change the one-cycle generator
or make every catalog candidate composable.

On the maintained mixed-surface Leppävaara graph, 77 of 80 reconstructed
cyclic blocks pass the complete-embedding checks. The largest block does not:
it has multiple apparent outer walks and non-simple/degenerate walks, although
all half-edge incidences are present. Exact experiments must use only verified
blocks until the imported planar embedding is repaired.

Implement this independently of cycle joining:

1. apply the profile to the planar graph, recompute cyclic biconnected blocks,
   and verify complete block-local face incidences and outer faces, including
   bounded-domain handling;
2. build directed outward/return access indexes, reachable seeds, and diagnostic
   dual layers;
3. adapt existing growth to deterministic multiband discovery with incremental
   metrics, early deduplication, and bounded local refinement;
4. validate and render candidates, preserving terrain/geographic alternatives;
5. implement the small linear reference model with both flow connectivity
   conditions and post-solve route validation, then verify incumbent, bound,
   gap, and termination reporting for the selected solver backend;
6. compare growth and the oracle on identical seed/domain/bucket constraints;
7. refine promising buckets using compactness bounds; add exact enumeration or
   multi-face expansion only where the measured quality/coverage warrants it.

Synthetic topology checks must include a missing/non-simple face walk, an
articulation joining two cyclic blocks, disconnected cyclic components, an
interior excluded region, contraction into the exterior, and a disconnected
fixed-zero complement. Access checks must include one-way reachability without
a return path, a connector that crosses the cycle before its nominal portal,
overlapping outward/return paths, and invalid attachment turns.

The first evaluation should answer:

- Does growth visibly proceed outwards from Ruusutorpan koulu?
- Do compactness and convexity prefer recognizable smooth loops rather than
  merely small or block-shaped boundaries?
- Which useful loops are lost by requiring every intermediate state to have a
  simple boundary?
- How many proposals, duplicates, retained states, and full geometry evaluations
  occur per seed and band, and where is runtime spent?
- How large a dual radius can the exact model solve, and what optimality gaps
  remain under a practical time limit?
- How far are heuristic candidates from the certified area optimum in the same
  perimeter bucket?
- Are explicit connector transitions needed before moving on to catalog
  composition?

Also measure which loops are recovered by local refinement or multi-face chunks,
whether access-ordered seeds preserve geographic coverage, and whether terrain
representatives survive selection. Optimize envelope-merge compatibility only
after the catalog produces useful building blocks.

## Mathematical and implementation references

- Bondy and Murty, [*Graph Theory*, Section
  10.2](https://www.facom.ufu.br/~nunes/teoria%20dos%20grafos/graph-theory.pdf),
  gives the plane-graph cycle/bond duality used by the exact formulation.
- Rehfeldt, [*Optimal connected subgraphs: Integer programming formulations
  and polyhedra*](https://doi.org/10.1002/net.22101), surveys flow and cut
  formulations for connected selected subgraphs.
- The [SciPy `milp`
  documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html)
  specifies the binary/integer linear solver interface available to the
  prototype.
- The [CVXPY mixed-integer and solver
  documentation](https://www.cvxpy.org/tutorial/constraints/index.html#mixed-integer-programs)
  describes boolean models and the available HiGHS and SCIP backends.

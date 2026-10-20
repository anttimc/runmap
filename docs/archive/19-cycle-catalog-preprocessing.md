# 19. Start-local cycle-catalog preprocessing

> **Archived 2026-10-09 — not an implementation plan.** Superseded by the
> [map-first plan](history/22-map-first-running-plan.md). Status statements and commands
> below describe the historical experiment. Its cycle-specific code was removed;
> see [archive recovery instructions](README.md).

> **Status: superseded.** This document describes the first overlapping,
> length-banded catalog prototype. It remains useful as an implementation and
> evaluation record, but its remaining roadmap is inactive. Continue with
> [continuous cycle refinement](21-continuous-cycle-refinement.md).

## Purpose

This is the first half of the two-level route search described in
[cycle-catalog route search](18-cycle-catalog-route-search.md). It discovers
reusable running cycles for a supplied starting point without using the final
route-length request. For the Leppävaara experiment the starting point is
Ruusutorpan koulu.

The catalog is deliberately visual and file-backed. Its purpose is to answer
whether outward exploration from the start finds recognizable local loops at
useful scales before cycle-combination search is implemented. A catalog may be
reused for several route lengths and training goals from the same start, but it
is no longer intended to be independent of the start.

## Revised preprocessing method

The detailed inner search is specified separately in
[start-local one-cycle search](20-one-cycle-search.md). This document describes
how that search populates and evaluates the reusable catalog.

The preprocessor takes the prepared graph, face topology, route profile, stair
policy, and a starting coordinate. The coordinate is snapped to the runnable
graph before discovery begins. Precompute directed distances outward from the
start and back to it using the reversed graph. Select a bounded set of reachable
seed faces in access-distance order with geographic spread and stable ID ties.
The containing or nearest usable faces are initial seeds, not the only seeds.

The first generator adapts existing face growth into a deterministic bounded
beam that emits into all length bands during the same run, followed by bounded
face-add/remove/swap refinement. Deduplicate before expensive geometry work and
update area and perimeter incrementally. This search is explicitly heuristic.
Dual breadth-first layers describe topology and bound selected experiments;
they count face crossings and must not be interpreted as geographic metres.

The linear dual-cut model from plan 20 is a small-instance reference oracle.
Before using it, recompute cyclic biconnected blocks after profile filtering,
then repair and verify complete block-local embeddings, including true
outer-face incidences, profile contractions, and excluded-face connectivity.
Compare growth and exact solutions on identical seed/domain/length constraints.
Do not make exact enumeration of every radius and narrow bucket a prerequisite
for the first useful catalog.

The starting point controls the discovery order and locality; it need not lie
on or inside every stored cycle. Each cycle records outward and return distances
at one or more portals. Initially require at least one portal with both directed
distances within the configured one-way access budget. Reconstruct paths only
for retained candidates. Normalize attachment to the first outbound and last
return intersection with the cycle, validate attachment turns, and measure
access-cycle and outward-return overlap under the repetition policy; outbound
reachability alone is insufficient. This gives the later
composition search enough information to decide whether to use a short access
stem without forcing all useful local loops to share a single edge at home.

Candidate cycles are collected into the same provisional 1, 2, 3, and 4 km
bands. The exact model may use narrower circumference buckets inside them, but
all searches reuse the same topology and start-access indexes. Exact runs use
a few domains and coarse buckets first, refining buckets using compactness
bounds only when useful. Equal boundaries are measured once and deduplicated
across seeds, bands, and solve instances. A
catalog cycle:

- is one geometric boundary with its legal directed orientation alternatives,
  stored separately from access and return paths;
- contains no profile-forbidden segment or stair;
- satisfies the 170-degree near-reversal constraint;
- retains its base faces, boundary segments, directed arcs, and growth
  or solver provenance; and
- records length, ascent, surface-independent route metrics, shape metrics,
  portals, centroid, bounds, dual layer, and outward/return access distances.

### Initial catalog selection

Use a deterministic greedy selector per length band. Retain representatives
across configurable geographic and ascent groups, then fill the remaining
budget by quality and newly covered boundary length. Record the group settings,
quality ordering, stable tie-breaker, and selection reason for reproducibility.
Measure compactness, convexity, turns, and access separately so the policy can
be changed after visual evaluation. A geometry-only Pareto filter is not safe
for this catalog: recording ascent does not help if all useful hilly options
have already been discarded. Preserve legal direction alternatives as well.

Use physical-length-weighted overlap:

```text
overlap(B1, B2) = length(B1 intersect B2) / length(B1 union B2)
```

Segment-count overlap changes when the same road is subdivided. Cache segment
sets and their lengths, and use an inverted segment index if pairwise checks
become a measured bottleneck. Avoid aggressive overlap limits before evaluating
composition: shared boundaries can make two otherwise useful cycles joinable.
Keep the validated candidate pool separately from the selected display catalog
so selection changes need not regenerate cycles.

Defer exact catalog-selection optimization until greedy coverage and composition
tests expose a concrete shortcoming. Any later optimum is relative to the
enumerated pool, not all possible cycles.

### Initial runnable-surface profile

The first start-local iteration relaxes the current `street-shoes` profile into
a maintained mixed-surface profile. It should:

- allow paved and other hard surfaces;
- also allow `gravel`, `fine_gravel`, and `compacted` paths;
- reject clearly soft or unmaintained surfaces such as `dirt`, `earth`,
  `ground`, `grass`, `mud`, `sand`, and `woodchips`;
- treat the generic `unpaved` value as unknown unless a more specific surface
  or maintenance attribute establishes suitability;
- reject unknown-surface `path` and `track` features unless another attribute
  positively indicates that they are maintained; and
- reject forest paths even when their surface alone would otherwise pass.

The last rule cannot be implemented reliably from the current surface tags
alone. It requires a forest/wood land-cover overlay or an equivalent enriched
environment classification. Until that enrichment exists, the implementation
must report forest suitability as unknown rather than silently treating every
gravel path as acceptable. A maintained street, footway, or cycleway merely
passing through wooded land should remain distinguishable from a forest path;
this distinction needs to be checked visually in the first experiment.

Stairs remain an independent policy. The first run continues to avoid them so
that changing the surface profile is the only new runnability variable.

## Implementation sequence

1. Recompute filtered cyclic biconnected blocks and verify complete block-local
   incidences, outer faces, profile contractions, and excluded regions as
   specified in plan 20.
2. Add the snapped start, outward/return access indexes, reachable seed policy,
   and budgets to catalog inputs. Cache identity includes graph/topology,
   profile/stair policy, enrichment version, generation settings, and schema
   versions; selection settings identify a separate view of the candidate pool.
3. Implement deterministic multiband growth, early deduplication, incremental
   metrics, and bounded local refinement. Keep single-face growth as a baseline
   and compare it with the existing multi-face chunk generator.
4. Select geographic and terrain representatives, render the catalog, and
   record expansion counts, cache hits, geometry time, and stopping reasons.
5. Add the small linear oracle and compare quality/gaps on matching bounded
   instances. Verify incumbent, dual-bound, gap, and termination fields from
   the selected solver before refining length buckets adaptively; defer conic
   optimization and exact catalog selection.
6. Introduce the maintained mixed-surface profile and verify gravel retention
   and soft-surface rejection. Evaluate it separately from algorithm changes.
7. Add forest/wood enrichment and explicit rejection reasons, then repeat the
   visual evaluation with the start, access/return paths, and cycle interiors.

The evaluation gate is that the visual catalog expands coherently from
Ruusutorpan koulu, includes familiar maintained gravel routes, excludes known
forest or unmaintained paths, and does not spend its early search budget on
distant disconnected areas. Algorithm comparisons first use a fixed profile
and fixed expansion budgets. The final profile gate also requires forest
enrichment; before then, forest suitability is explicitly unknown. Compare
catalog coverage and manual acceptance alongside runtime and oracle gaps.

## Command and outputs

After graph and topology preparation, run:

```sh
PYTHONPATH=src .venv/bin/python -m runmap.cli routes catalog \
  leppavaara-flat-10k
```

The ignored prepared outputs are:

- `cycle-catalog.json`, containing the validated candidate pool, selected cycle
  IDs, seeds, generation/selection settings, and summary statistics; and
- `cycle-catalog.geojson`, containing the directed cycle geometry and primary
  metrics for inspection.

The reproducible diagnostic renderer is:

```sh
MPLBACKEND=Agg MPLCONFIGDIR=/tmp/runmap-matplotlib PYTHONPATH=src \
  .venv/bin/python -m runmap.experiments.plot_cycle_catalog \
  --base data/paths/prepared/leppavaara-flat-10k/topology/topology-segments.geojson \
  --catalog data/routes/prepared/leppavaara-flat-10k/cycle-catalog.geojson \
  --experiment config/routing-experiments/leppavaara-flat-10k.json \
  --output artifacts/cycle-catalog.png
```

The four-panel image uses the same extent for every band. Ruusutorpan koulu is
shown as the black star and is also the origin of the access indexes that order
catalog discovery.

## Current measured result

| Measurement | Result |
| --- | ---: |
| Profile superfaces available | 1,497 |
| Selectable block-embedding faces | 1,502 |
| Access-ordered seeds | 12 |
| Multiband traversals | 24 |
| Expanded / pruned growth states | 1,334 / 32,536 |
| Duplicate proposals removed early | 1,234 |
| Generated distinct cycles | 169 |
| Selected catalog cycles | 48 |
| Selected per length band | 12 |
| Stair distance | 0 m |
| Runtime | 12.5 s |

The implementation now computes directed outward and return indexes from
Ruusutorpan koulu, selects reachable seeds in access order with spatial
spacing, and performs one shared growth traversal per seed and variant. Each
traversal retains a small beam in each coarse perimeter bucket and emits into
all four length bands. Candidate area and perimeter are updated incrementally,
both legal directions and bounded access portals are retained, and catalog
overlap is weighted by physical segment length. The full generated pool is
stored separately from the selected display cycles. Shape output includes the
isoperimetric quotient, its reciprocal `compactness`, and convexity ratio.

The maintained mixed-surface profile allows specifically tagged gravel,
fine-gravel, and compacted routes while continuing to reject generic unpaved
and unknown-surface paths and tracks. Forest suitability is still unknown
because the graph has no land-cover enrichment.

Profile-filtered block reconstruction currently finds 80 cyclic blocks: 77
have a complete valid embedding and three do not. The invalid set includes the
largest block (5,586 segments and 1,365 selectable faces), whose walks contain
five apparent outer faces plus non-simple and degenerate walks. No segment
incidences are missing. This points to crossings or degenerate imported
geometry rather than a half-edge accounting omission. The exact dual-cut
oracle must therefore initially be restricted to the 77 verified blocks; it
must not claim coverage of the main block until that embedding is repaired.

## Current evaluation

The shared traversal preserves all four requested bands: it generated 38, 43,
44, and 44 candidates for the 1, 2, 3, and 4 km bands respectively, selecting
12 from each. Eleven of twelve seeds generated candidates; the unproductive
seed remains in the diagnostic output rather than being silently replaced.
The explicit prune count also makes clear that this is a bounded heuristic,
not an optimal search.

The next algorithmic step is bounded add/remove/swap refinement and actual
access-path reconstruction with first/last cycle-intersection normalization.
After that, implement the linear oracle on verified blocks and compare it with
growth on identical domains. Repairing the large invalid embedding, forest
enrichment, and manual acceptance/rejection remain separate evaluation tasks.
More elaborate composition optimization should wait until these first rooted
envelopes have been reviewed visually.

## First rooted composition layer

A simple envelope-composition experiment now uses the generated 1 km candidate
pool as modules. It constructs the compatibility graph using only pairs with
disjoint face interiors and at least five metres of shared boundary. One
access-near root is selected from each of up to three nontrivial compatibility
components. From every root, breadth-first composition adds a module to the
current boundary by symmetric difference and rejects results that do not remain
one legal directed cycle. A bounded beam is retained at each module count.

Every composed cycle records its root, parent, newly added candidate, complete
module list, last and cumulative removed shared boundary, marginal length, and
the usual route and shape metrics. This makes the derivation reproducible; it
does not yet reconstruct the access paths or optimize the root set globally.

The first Leppävaara run produced 16 envelopes from three roots: four with two
modules, six with three, and six with four. Their distance ranges are
1.78–1.98 km, 2.66–2.92 km, and 3.16–3.78 km respectively. Only one root
currently grows beyond two modules, which exposes the sparse and uneven
compatibility of the original standalone candidate pool. This is a diagnostic
result rather than a final catalog-selection policy.

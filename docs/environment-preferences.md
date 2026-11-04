# Environment preferences: proposed design and experiment

Status: pilot acquisition, source inspection, and first raster baseline available,
2026-11-02.
A bounded OSMnx feature downloader, Leppävaara pilot configuration, and
[source inspection map](guide.md#environment-source-inspection) are implemented; see
[acquisition commands and limitations](development.md#acquire-environment-pilot-data).
The [four raster layers and review-candidate overlay](guide.md#experimental-environment-rasters)
are implemented for the pilot. Complete-context validation, calibrated meanings,
models, and routing preferences remain proposed. This document owns the proposed
design and experiment protocol; the
[roadmap](roadmap.md#planned-environment-preferences-experiment) owns outstanding
work and delivery gates. Existing capabilities remain in the [guide](guide.md).

The [first acquisition report](archive/history/2026-11-02-environment-acquisition.md)
records the 2026-11-02 pilot, checksums, feature counts, and remaining coverage
limitations. It does not establish model quality or complete context coverage.
The [inspection progress report](archive/history/2026-11-02-environment-inspection.md)
records the source-map checks, initial user review, and Leppävaarankatu road-class
calibration case. Class-specific road styling remains planned.
The [first raster report](archive/history/2026-11-02-environment-rasters.md) records
the mapped-context bundle and checks after the source-interpretation review.
The [road-noise update](archive/history/2026-11-02-road-noise-proxy.md) records
the replacement of linear traffic decay with the reference-car intensity model.

## Objective and first deliverable

Let a runner explore forest surroundings, proximity to lakes/sea, traffic-related
noise and discomfort, and wetland presence independently, then use supported
attributes as route preferences.
The first deliverable is an inspectable forest visualization in one familiar
capital-area region, with water and road context visible at the same locations.
Routing integration follows spatial-field and local-runner validation.

The learning objective is to use PyTorch to estimate surroundings from nearby
entities and test whether learning improves a transparent geometric baseline.
A useful baseline can ship without a learned model if the experiment does not
demonstrate an improvement.

## Spatial meaning and overlapping outputs

Keep mapped objects, estimated surroundings, and personal preferences separate:

| Quantity | Meaning | Initial representation |
| --- | --- | --- |
| Woodland coverage | Mapped or independently verified woodland footprint, including woodland inside parks/reserves and polygon holes | Source polygons and per-cell area fraction with evidence provenance |
| Greenery, G | Woodland surroundings plus weaker park/reserve contributions within their polygons | Continuous score in [0, 1] |
| Park coverage, P | Mapped `leisure=park` footprint, excluding nature-reserve designation alone | Internal per-cell fraction used by Greenery |
| Water proximity, W | Proximity to any mapped water-polygon boundary or coastline | Continuous score in [0, 1], with separate interior/distance evidence |
| Traffic exposure, T | Proxy for traffic-related noise and running discomfort from nearby roads | Continuous score in [0, 1] |
| Wetland presence, H | Mapped wetland footprint, independent of woodland or open water | Per-cell area fraction in [0, 1] with separate validity |
| User preferences | Tradeoffs between route length and environment attributes | Separate nonnegative routing weights |

G, W, T, and H do not sum to one. A wooded lakeside wetland beside a major road
can score highly on all four. Do not subtract traffic or wetlands from greenery
or assign one winning class per cell. Retain continuous fields; thresholded
contours are display products, not the underlying data model.
Park coverage is an internal Greenery input and can overlap woodland, water
context, and wetlands. It is not exposed as a separate preference class.

Land cover and surroundings have different targets. A path just outside a
woodland polygon may have wooded surroundings. Water proximity does not imply
a view of water or access to its shore. Road-derived exposure does not measure
current traffic, noise, air quality, or safety. The initial feature offers
traffic avoidance as a soft preference; a guarantee of no nearby traffic is
outside this experiment.

### Source interpretation after pilot review

The user's source-map review identified these requirements before field generation:

- **Woodland inside parks and nature reserves:** retain park/reserve boundaries
  as weaker greenery evidence within the polygon, not automatic woodland
  labels. Include explicitly mapped woodland within them at higher weight. Where a wooded park
  lacks woodland geometry, record the missing evidence and obtain independently
  reviewed wooded subareas or an appropriate canopy/land-cover source. Do not
  fill an entire park/reserve as forest or treat its unclassified interior as
  confirmed nonforest. Nearby context may help a model, but a designation alone
  cannot establish tree cover.
  The park diagnostic uses `leisure=park` only; reserves have their own
  source toggle and remain separate evidence. A reserve designation alone does
  raises greenery at the configured weaker weight, while keeping the park
  raster and woodland diagnostic separate. V3 uses a maximum of the weighted
  components, so overlapping designations do not add together.
- **Wetlands:** retain their own geometry, support mask, model output, and
  optional avoidance weight. A wooded wetland may have both high G and high H;
  some users may prefer forest while avoiding wetlands. H initially describes
  mapped wetland presence, not wet footing, flood risk, or path accessibility.
  Review paths and boardwalks through wetlands before interpreting route exposure.
- **Water:** use all mapped water-polygon boundaries for inland shoreline
  distance and coastline geometry for coastal distance. Keep water interiors
  identifiable where complete area geometry exists. Line streams/ditches remain
  inspectable context and are excluded from default W, while polygonal water
  bodies contribute regardless of lake classification. Reservoirs, basins, ponds,
  and unclassified water polygons are accepted inputs, not review candidates. Do not infer
  ocean areas by closing arbitrary clipped coastline lines.
- **Roads:** target traffic-related noise and running discomfort, with less-busy
  streets contributing less than major roads at comparable distance. Class is
  an initial prior, refined by speed, lanes, and traffic-volume evidence if
  available. Keep missing attributes explicit. Evaluate distance decay and
  possible shielding/grade separation against local judgments. The initial field
  is a relative exposure proxy, not measured sound levels or live traffic.

Keep raw OSM tags unchanged. Version these interpretation rules separately from
acquisition, and show which source and rule contributed to each derived field.

## Proposed preparation and delivery boundaries

```mermaid
flowchart LR
    OSM[OSM environment extract] --> Objects[Normalized spatial objects]
    Objects --> Features[Raster feature channels]
    Features --> Baseline[Geometric baseline]
    Features --> Model[Offline PyTorch inference]
    Labels[Independent reviewed labels] --> Training[PyTorch training]
    Training --> Model
    Baseline --> Fields[Versioned environment fields]
    Model --> Fields
    Fields --> Tiles[Map tiles and inspection]
    Fields --> Intervals[Physical segment intervals]
    Intervals --> Numeric[Prepared numeric routing features]
```

Environment objects and fields are independent of routing topology. Prepare
them offline; panning and route requests never fetch OSM or run a neural network.
Use bounded spatial reads for tiles and inspection. A missing environment
artifact must leave existing terrain and routing services usable.

Proposed ownership follows the existing [architecture](architecture.md):
`sources/` handles acquisition, a new `environment/` package handles object
normalization and field preparation, `experiments/` owns training/evaluation,
`paths/` joins fields to physical segments, and `api/` plus `web/src/` deliver
the visualization. Shared routing and native code consume prepared features.
Use data-only containers and module-level functions; a PyTorch model may use
`nn.Module` for its parameter lifecycle. Keep PyTorch in an optional experiment
dependency group rather than an interactive-server requirement.

## Source acquisition and geometry

The initial implementation uses OSMnx's feature API and raw response cache.
It retains assembled geometry and source tags without replacing the existing
routing graph. Pyosmium's area assembly over regional `.osm.pbf` files is a
candidate for a later import path, especially if bounding-box discovery omits
large enclosing areas. The initial query does not satisfy the complete-geometry
gate below until independently checked; successful download alone is insufficient.

Reuse OSM acquisition and provenance conventions, with an independent environment
query. The existing runnable-highway extract cannot establish environment
coverage: it excludes forest geometry and many major roads.

- Include `natural=wood` and `landuse=forest`, water areas with their water-body
  types, waterways, wetlands with available subtype tags, and relevant shorelines.
  Preserve parks/reserves and other green areas as distinct candidate evidence;
  investigate missing wooded subareas rather than using a designation as cover.
  Preserve water-body types for inspection while accepting all polygonal water. Preserve excluded line streams
  and ditches for inspection without allowing them to raise default W.
- Acquire road context independently of pedestrian eligibility, including major
  roads. Preserve road class, speed/lanes where available, and bridge, tunnel,
  layer, access, and service attributes. Missing attributes get explicit masks.
- Retain buildings as a possible contextual feature. Trees and tree rows are
  optional subsequent inputs; omit them explicitly from the initial manifest.
- Assemble complete multipolygon relations and member ways, respecting inner
  rings. Retrieve complete geometries intersecting the analysis region and halo,
  rather than selecting objects only by contained vertices. Record incomplete
  relations and geometry repairs; do not silently use broken areas as labels.
- Preserve source type/ID, tags, geometry, source time, query, bounds, checksums,
  attribution, and licensing. Deduplicate shared objects and avoid double-counting
  overlapping woodland polygons when calculating union coverage.

The initial acquisition halo is 300 m. Acquire at least that halo around the
display core; enlarge it if feature computation or the model receptive field
requires more context. Track valid support explicitly at boundaries. Every
derived tile must use the same grid origin and full required context before
cropping its core.
Road-noise v2 has no finite 300 m propagation cutoff. It uses retained sources
and separately flags where an exterior source could dominate under configured
strength limits. Expand acoustic context before treating such cells as supported;
the retained-halo flag alone does not establish noise coverage.

A successfully downloaded extract establishes acquisition coverage, not complete
real-world mapping. Store these separately: acquisition/geometry validity,
attribute missingness, independent label validity, and estimated model uncertainty.
Absence of a forest or road tag is not reliable evidence of absence on the ground.

## Grid, features, and geometric baseline

The delivered baseline uses a 10 m EPSG:3067 grid with 2 m coverage samples;
see the [artifact contract](reference/prepared-data.md#environment-raster-bundles)
for delivered channels and approximations. Compare a 5 m grid on
narrow paths and woodland edges before deciding the delivered resolution.
The grid must not change pedestrian connectivity or access permissions.

Candidate model inputs extend the delivered channels with woodland coverage within
25/100/300 m, park/reserve membership, wetland fraction and subtype,
separate lake-shore and sea-shore distances, road
distance and density by class, available road attributes, building coverage,
and validity/missingness masks. Use metric geometry and point-to-line/polygon
distances, not distance to object centroids. Compare input channels through
ablations rather than assuming all of them help.

The first baseline is deliberately reproducible. The rules below distinguish
delivered mapped context from later evidence and calibration improvements:

- F remains the diagnostic mean eligible woodland union coverage in a 100 m disk. Version 1 uses
  explicit woodland polygons, including those mapped within parks/reserves;
  independently reviewed wooded subareas remain an additional evidence step.
  Retain the footprint and provenance
  separately from F. Unresolved tree cover needs an evidence-gap indicator;
  a numeric mapped-only baseline must not imply confirmed forest absence.
- G is `max(F, 0.5 P, 0.5 R)` by default in v3. P/R are park/reserve cell
  coverage with no spatial blur. Keep designation weights configurable below
  woodland's fixed weight 1; forests can contribute more strongly than a
  designation alone. Park and reserve coverage remain diagnostic inputs only.
- W is `max(0, 1 - d / 100 m)`, with d the minimum distance to any mapped
  water-polygon boundary or coastline. Water interiors and distances remain
  separately inspectable. V1/v2 selected explicitly tagged lakes only; v3 accepts
  all mapped water areas. Line streams/ditches remain excluded; missing mapping
  must not silently become evidence of absence.
- H is the union fraction of mapped wetland within the grid cell, with separate
  evidence validity. It can overlap greenery and is not subtracted from G.
- T now uses the [v2 reference-car noise proxy](reference/prepared-data.md#road-noise-proxy-version-2):
  reference intensity at 10 m, effective motorway/trunk intensity attenuation
  `1/r^1.5` or sparse-street `1/r²`, logarithmic dB, and a configurable discomfort scale.
  The class factors are uncalibrated relative-intensity priors. The original
  linear 300 m baseline is retained only for comparison/reproduction. Exclude
  mapped motor-vehicle prohibitions; absent access tags remain an assumption
  recorded in configuration. Compare Leppävaarankatu and motorway surroundings
  at matched distances before accepting reference levels, source types, and
  discomfort thresholds. Evaluate speed/lane, flow, shielding, and ground-effect
  refinements separately, with an explicit policy for missing evidence.

These are initial proxy definitions, not calibrated probabilities or established
perceptual distances. Save all radii, tag rules, and amplitudes in configuration.
The dominant-source road baseline avoids treating duplicated road geometry as increased
exposure; a road-density model is a later comparison. Preserve bridge/tunnel
evidence for review without assuming that grade separation removes exposure.
The baseline describes mapped context and must display that limitation even
where acquisition coverage is valid.

## Labels and missing-data experiment

Choose three nonadjacent familiar study areas within existing prepared coverage,
spanning wooded parks/reserves, woodland interiors/edges, wooded wetlands,
water-polygon/coastal shores, line-stream/ditch controls, and contrasting road corridors. Freeze
their bounds and assign training, validation, and final test areas before model
tuning. Use guard bands at least as wide as the maximum feature/model context,
and prevent shared large source objects from leaking between splits by grouping
them or excluding affected boundary samples.

Initially review 100 locations per split, including confirmed positive and
negative examples for each attribute and overlapping cases. Keep a representative
sample separate from deliberately selected difficult cases. Record coordinates,
review date, evidence/source identity, and per-attribute validity. Use a fixed
rating rubric: 0 absent, 1 low, 2 medium, 3 high. Define greenery with woodland
and designation components recorded separately, water proximity, wetland presence, and traffic-related noise/discomfort
separately with example locations; do not
ask reviewers for a single overall route-quality score. Record disagreements
and unknowns rather than forcing labels. Re-review at least 20% of locations
with a second reviewer where possible; otherwise report the single-reviewer
limitation. Expand the sample if an attribute lacks sufficient positive/negative
examples or model comparisons remain inconclusive.

OSM polygons can provide weak woodland labels, but cannot independently validate
predictions made from those same polygons. Use field review or independently
verified imagery/land-cover evidence, with dates and resolution recorded. Finer
output grids do not create finer evidence from a coarse source.

For missing-data recovery, remove whole objects or contiguous mapped patches
from model inputs while retaining independent evaluation labels. Test both
artificial omissions and naturally incomplete mapping. Removing isolated raster
pixels is insufficient. Keep this task separate from reproducing known OSM
geometry: nearby tags alone cannot recover arbitrary missing forests.

## PyTorch model comparisons

Run comparisons in order, with identical splits, targets, and feature support:

1. Geometric baseline, including its support masks.
2. Small per-cell MLP using prepared multiscale summaries, to test whether learned
   feature combinations help without learned spatial aggregation.
3. Small CNN or U-Net over raster patches, to learn spatial arrangement. Record
   the effective receptive field and avoid patch/tile padding artifacts.
4. Optional heterogeneous GNN if raster errors motivate entity-based aggregation.
   Use query cells, woodland polygons, water entities, and road segments as node
   types; relations carry distance, containment/overlap, and structural evidence.
   Query cells must cover the area, not only existing routing edges.

Use separate output heads for each attribute. Continuous rubric ratings can be
normalized to [0, 1] and trained with a masked regression loss. For explicitly
binary labels, use independent logits and masked `BCEWithLogitsLoss`, not a
softmax. Normalize losses per observed attribute before combining them; unknown
targets contribute no supervised loss. Skip batches with no valid targets.

For binary predictions evaluate calibration separately; a sigmoid value is not
an uncertainty estimate. Report per-attribute label counts, model disagreement
if using multiple seeds, and performance by evidence completeness. Preserve
coverage masks regardless of predicted confidence. Add imagery only as a
separately evaluated input experiment with acquisition/licensing defined first.

## Visualization and inspection

The existing application includes independent source geometry and first-version
greenery, water proximity, traffic-exposure, wetland, and review-candidate
raster overlays. Click inspection shows all four scores, numeric channels,
halo support, and nearby contributing evidence; see the guide above. These are
mapped-context scores, not learned predictions. Full unknown-coverage mapping,
additional source evidence, and baseline/model comparison remain proposed.

For subsequent field experiments, export matched review panels and compare
layer combinations. Use a forest heatmap, water contours, and traffic hatching or outlines
so overlaps remain visible. Include legends and baseline/model comparison with
matched scales. Inferred values must be distinguishable from source geometry.

Click inspection should show all attribute scores, mapped source objects,
distances/coverage summaries, validity, model/source versions, and whether the
value is a mapped-context baseline or learned estimate. Keep acquisition and
training details in developer inspection rather than ordinary preference controls.
Check source holes, tile seams, zoom changes, keyboard/mobile use, rapid panning,
cancellation, stale responses, and combinations with existing terrain/path layers.

## Experiment evidence and decision criteria

Save configuration, source and label manifests, split assignments, feature/grid
versions, code revision and dirty-state identity, dependency versions, training
seeds/checkpoints, machine, runtime, and peak memory. Store generated outputs in
a dedicated `artifacts/environment-preferences/` run directory. Write dated
reports under `docs/archive/history/` when experiments actually run; no result
is claimed by this plan.

Report per-attribute MAE against normalized ratings, thresholded precision/recall
and IoU where binary labels are valid, and calibration where probabilities are
claimed. Evaluate forest boundaries, mixed environments, and missing-context
cases separately. Report representative-area and challenge-set results separately.
Show baseline/model differences and source evidence at saved review locations.
Use spatial-block uncertainty estimates where sample size permits; do not count
neighboring cells as independent evidence of improvement.

Choose architectures and thresholds using validation data only. Before opening
the final test results, freeze the practical improvement margin and acceptable
regression limits from reviewer disagreement and validation observations. Promote
a learned model only if it improves the intended target over the baseline across
spatial holdouts, preserves useful overlaps, and has acceptable preparation cost.
Otherwise retain the baseline and document failures. Report every trained seed,
not only the best run.

Measure preparation time, artifact size, offline inference memory/time, cold/warm
tile and inspection p50/p95 latency, and browser interaction on recorded hardware.
Set numerical delivery budgets from the first baseline measurements before
expanding coverage. Field generation and tile delivery need separate budgets.

## Later routing integration

After field review, sample G/W/T/H into ordered physical-segment intervals with
validity/support and artifact identity. Handle woodland boundaries and partial
segments explicitly; refine sampling or intersect geometry where coarse cells
miss narrow features. Follow the existing projected/geodesic chainage contract
in [prepared data](reference/prepared-data.md#published-graph-and-measurements).
Both directed arcs share environmental context initially; access remains a
separate policy. Repeated traversals incur repeated exposure costs.

An initial length-equivalent cost is:

\[
C(e)=\int_e [1+w_g(1-G(s))+w_w(1-W(s))+w_tT(s)+w_hH(s)]\,ds,
\qquad w_g,w_w,w_t,w_h\geq0.
\]

Here ds is graph-distance chainage in metres and the weights are dimensionless.
Wetland avoidance has its own weight, initially Off, so it does not cancel
woodland evidence or affect users who have no wetland preference.
This adds nonnegative penalties and preserves distance as a lower bound; it
does not define the full existing hill/shape objective. Review native/reference
cost integration and admissible heuristics before enabling controls. Prove that
all environment weights Off reproduce existing routes and costs.

Do not substitute zero for unknown fields. Before enabling each preference,
choose and document either a validated imputation policy with separate unknown
reporting or a supported-coverage restriction. Until that decision passes review,
keep the affected control unavailable. Show route coverage and environment
summaries separately from preference strength; lack of evidence must not become
an apparent quiet or forest-free area.

Version the field artifact by source, feature rules, grid, and model/checkpoint.
Version its segment join by network identity and sampling rules. Extend network
and numeric artifacts together, preserving segment order, bounded loading,
partial-endpoint semantics, and native/reference equivalence. Calibrate individual
preferences and combinations against Off using saved familiar routes; report
length, exposure, unknown fraction, expanded states, latency, and runner judgments.

## Documentation transition

As stages ship, move delivered usage to the guide, component boundaries to the
architecture, configuration/commands to development, artifact contracts to
prepared-data references, and accepted cost definitions to routing mathematics.
Remove completed tasks from the roadmap, retaining outstanding runner acceptance
explicitly. Keep this proposal clearly marked or archive it once superseded.

## Technical references

- [OSMnx feature API](https://osmnx.readthedocs.io/en/stable/user-reference.html#module-osmnx.features)
  for pilot acquisition and [Pyosmium area processing](https://docs.osmcode.org/pyosmium/latest/reference/File-Processing/)
  for the candidate regional-file path.
- [OSM forest tagging](https://wiki.openstreetmap.org/wiki/Tag:landuse=forest)
  and [multipolygon geometry](https://wiki.openstreetmap.org/wiki/Relation:multipolygon).
- [PyTorch BCEWithLogitsLoss](https://docs.pytorch.org/docs/2.9/generated/torch.nn.BCEWithLogitsLoss.html)
  for independent binary targets.
- [PyTorch Geometric heterogeneous learning](https://pytorch-geometric.readthedocs.io/en/latest/notes/heterogeneous.html)
  for the optional entity-based model.

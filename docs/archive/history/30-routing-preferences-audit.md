# Routing preference data audit

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

Status: existing local dataset audited, 2026-10-15. Context acquisition, crossing
event attribution, and local runner review remain open gates for preferences
that need them. This records the first audit increment of
[the routing preferences plan](29-routing-preferences.md).

## Findings and readiness

| Preference | Available evidence | Preparation needed |
| --- | --- | --- |
| Hills | Signed, chainage-aware terrain intervals on every prepared segment; bridge/tunnel flags survive | Reuse measurements; add bounded directional costs, uncertainty fallback, and diagnostics |
| Traffic lights | Legacy and modern crossing tags in raw OSM, including multi-node crossings | Preserve dropped node/way tags; attribute crossing passages before enabling costs |
| Busy roads | Runnable street geometry and an incidental ref 101 road sample | Acquire complete buffered road context independently of runnable eligibility |
| Green surroundings | No usable polygon context in this extract | Acquire tagged ways and multipolygon relations with complete member geometry |

The next implementation can start with feature/cost composition and hills.
Signals and environmental controls must remain unavailable until their data
and attribution gates pass. All-Off must preserve current routing.

## Reproduction and identity

From the repository root, with the existing Python environment:

```sh
uv run --offline --no-sync python -m runmap.experiments.audit_routing_preferences \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  docs/30-routing-preferences-audit.json
```

The [machine-readable report](30-routing-preferences-audit.json) contains exact
identities, tag-value counts, raw/prepared fixture pairs, and geometry. The tool
checks the source SHA-256 against graph provenance, the measurement checksum,
and graph/measurement digest and segment identity before reporting. It reads
local artifacts without rebuilding or downloading. Nearest examples are selected
around Ruusutorpan koulu; two crossing fixtures use fixed OSM IDs. They are audit
examples, not an exhaustive acceptance suite.

- Source: `data/paths/raw/leppavaara-flat-10k/osm-network-boundaries-101.json`;
  downloaded 2026-10-06 at 12:31:09 UTC, OSM base timestamp 12:28:50 UTC.
- Source SHA-256:
  `017d294b1e6dc1d0461d62fda45e79278b0835ea62522d2362bd68877d055d18`.
- Query bounds, south/west/north/east:
  `[60.1740814314, 24.7051230198, 60.2569525184, 24.8368305750]`.
- Experiment: `leppavaara-flat-10k`, schema 2, config digest
  `5cadb447172475c5231da2a5a5aa9e3bd121a60791caa7831b0fd1062c56a1ea`.
- Prepared graph schema 1; measurement schema 1; graph digest
  `b74d86e5dcebdb20802ec502ed024214d8950f46fa2b0d7c9c589188d028d81d`.
- Terrain manifest `leppavaara-98e1c3956d293f61`, spectral thin-plate wavelength
  40 m, sampling interval at most 10 m per geometry edge, projected EPSG:3067.
- The graph does not record the Git revision or preparation date. The report
  records current importer/terrain code hashes; these do not prove which code
  produced the historical artifacts. Add a preparation implementation version
  when introducing preference features.

The source has 140,786 nodes, 33,160 ways, and no relations. Preparation keeps
30,423 ways as 69,707 physical segments and 139,414 arcs, with 55,627 vertices
and 2,036.809 km of graph length. All current segments have both directions;
future costs must still support directed eligibility and asymmetric hills.
There are 398 undirected components. These counts describe the acquired graph,
not the completeness of local OSM or pedestrian access.

## Hills and uncertainty

There are 278,839 measurement intervals and 2,042,138.409 m of projected sampled
chainage, all with valid numerical terrain samples. This differs from graph
distance because graph lengths use geodesic distances and sampling uses the
projected CRS. Use `graph_start_m`/`graph_end_m` to resolve routing partials and
the corresponding projected span to integrate grade features; do not directly
interchange the two chainage systems.

Numerical coverage does not establish path-elevation accuracy:

| Quality flag | Intervals | Projected length |
| --- | ---: | ---: |
| Bridge | 977 | 7,396.807 m |
| Tunnel | 294 | 2,142.142 m |
| Stairs | 1,274 | 6,827.219 m |
| Pedestrian-area perimeter | 5,162 | 24,989.357 m |

Flags may overlap. The union of bridge, tunnel, and missing-terrain intervals is
9,538.950 m. Missing-terrain intervals are absent in this dataset and need a
synthetic fixture. Existing measurement tests already exercise gaps, reversed
grades, and partial intervals.

Signed grade ranges from -63.386% to +56.335%; 1,539 intervals exceed 20% in
absolute magnitude. The steepest absolute example is a 1.198 m footway segment,
`w1136272384p0`, with no special quality flag. Investigate short geometry and
ground-surface artifacts before treating those values as physical running grades.
The audit records observations without correcting source data.

Proposed first implementation policy: retain every legal connection. Bound the
grade response; for missing, bridge, and tunnel intervals apply the maximum
per-metre hill penalty of the selected non-Off level. This prevents unknown
terrain from becoming a free flat shortcut without inventing a measured grade.
Report fallback distance separately, preserve quality flags, and show measured
ascent/descent as estimates with trustworthy coverage separate from numerical
coverage. With Off, the fallback contributes zero. Review this conservative
policy against the bridge/underpass fixtures before enabling the control.
Stairs remain governed by shared eligibility; area perimeters remain approximate
geometry. Both retain their warnings even with valid terrain samples.

## Signal evidence and event semantics

The source has 464 nodes with `crossing=traffic_signals` and four with
`crossing:signals=yes`; their union is 467. Of these, 458 are vertices in the
prepared graph and 455 still carry recognizable signal evidence. Two of the
467 source nodes are modern-tagged tram crossings; six signal-evidence nodes
in total carry railway tags. These are evidence counts,
not counts of independent pedestrian signal events or coverage percentages.

The importer retains node `crossing` and `highway`, but drops every
`crossing:signals`, `crossing:island`, `crossing:markings`,
`traffic_signals:direction`, and `railway` tag. Three graph vertices lose their
only explicit signal evidence:

- `6581850952`, `(24.8110282, 60.2126795)`: `highway=crossing`,
  `crossing:signals=yes`, no legacy crossing value.
- `7748153291`, `(24.8101982, 60.2137176)` and `7748153292`,
  `(24.8102485, 60.2137049)`: `railway=tram_crossing`,
  `crossing:signals=yes`. Preserve the railway evidence before deciding how to
  group or score these.

Way preparation also drops `crossing`, `footway`, and `cycleway`: the retained
source ways include 20 legacy signal-tagged ways, 356 `footway=crossing` ways,
and 372 `cycleway=crossing` ways. The report provides counts by tag and whether
the corresponding way survived. Node tags still force graph splits, so source
IDs remain available for recovering the lost evidence.

The tagging documentation distinguishes
[crossing signal evidence](https://wiki.openstreetmap.org/wiki/Key:crossing:signals)
from [traffic-signal nodes](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dtraffic_signals),
which can describe either pedestrian or vehicle signals. The source contains
89 `highway=traffic_signals` nodes; those need traversal attribution rather than
automatic pedestrian charges. Crossing geometry can span several ways and
islands; see [crossing-way documentation](https://wiki.openstreetmap.org/wiki/Tag:footway%3Dcrossing).

Two concrete topology findings constrain the event model:

- Way `98279693` near the school has signal nodes `1137173762` and `1137173716`,
  connected by `w98279693p1` (7.474 m). Counting nodes or both adjacent arcs
  would charge several times for one potential passage. The dataset has 186
  segments with signal-evidence nodes at both ends; proximity alone cannot
  determine whether they share one control phase.
- Node `38461548` at Numersinkatu joins two cycleway parts and two residential
  road parts. Visiting that node alone does not identify the crossing movement.

Event contract for implementation: charge once for a completed directed passage
through a mapped crossing event, charge again on a later passage, and preserve
event identity through endpoint splits. Starting or turning around inside an
event charges only when the movement crosses its charging boundary; touching
that boundary or reversing before crossing does not add another passage.
Do not infer synchronized stages from closeness or charge every signal node.

The current audit cannot establish control-phase grouping or assign all branch
movements from the reduced source. Retain crossing-way/island/railway tags first,
then record the event boundaries and movement expectations for the fixtures.
Use additive directed-arc charging only for events with unambiguous passage
arcs. Where the charge depends on entry/exit, retain arrival/event context in
search state; the branching fixture prevents assuming node-only search is
sufficient. Event attribution and avoidable/unavoidable route examples remain
required before enabling traffic-light preference.

## Road and green context

Acquisition requests runnable highway classes plus highway ways with `ref=101`.
It includes 91 trunk, eight motorway, and two trunk-link ways, all rejected by
runnable preparation. It includes no primary, secondary, or tertiary roads.
The raw ref 101 geometry can supply a future exposure fixture, but this selected
sample cannot establish surrounding road coverage. Current graph preparation
also drops `maxspeed` and `lanes`; retain those in a separate context artifact
if used as evidence. Do not change pedestrian eligibility to obtain road context.

There are no park/wood/forest polygons or multipolygon relations in the source.
Incidental tags include one recreation-ground element, a tree, a stone, a cave
entrance, bleachers, and a slipway. They do not constitute a green-area dataset.
The audit therefore cannot select honest local green/non-green or private-green
fixtures yet. Start the later polygon audit with
[parks](https://wiki.openstreetmap.org/wiki/Tag:leisure%3Dpark),
[woods](https://wiki.openstreetmap.org/wiki/Tag:natural%3Dwood), and
[forests](https://wiki.openstreetmap.org/wiki/Tag:landuse%3Dforest); these are
candidate evidence classes, not a final inclusion whitelist.

Context preparation must fetch roads and candidate green ways/relations with
complete geometry independently of the runnable network, record its own source
snapshot and checksums, and build a projected coverage mask. Choose influence
ranges explicitly; the context acquisition buffer must cover at least the
largest influence range plus geometry/sampling margins around the actual route
query domain. Full ways may extend outside an Overpass query box; their presence
does not establish complete context there. The current context buffer and
contextual coverage are unknown. Mark edge observations unavailable rather
than interpreting omitted roads as quiet or omitted polygons as non-green.

After acquisition, fixtures must cover parallel/crossing major roads,
grade-separated paths, parks/woods/edges, private green land, and green corridors
beside busy roads. No environmental preference can pass its data gate from
the current snapshot alone.

## Local fixtures and next steps

The report stores 13 source-derived fixtures with IDs and coordinates:

| Fixture | Prepared segment or source node |
| --- | --- |
| Flat corridor, 70.572 m | `w33925333p16` |
| Directional hill, 107.184 m; estimated +4.661/-0.280 m | `w642719209p1` |
| Rolling path, 152.491 m; estimated +4.534/-4.433 m | `w575610022p0` |
| Bridge ground estimate | `w171699102p0` |
| Ruusutorpanpolku underpass ground estimate | `w16117809p0` |
| Stairs | `w1426560342p0` |
| Missing surface / pedestrian-area perimeter | `w1426560351p0` |
| Extreme sampled grade | `w1136272384p0` |
| Multi-node crossing | `1137173762`, `1137173716` |
| Crossing with road/path branches | `38461548` |
| Modern signal tag, including two tram cases | `6581850952`, `7748153291`, `7748153292` |

These fixtures establish source and preparation observations. They have not
received local runner validation and do not prove avoidability or route quality.

1. Add versioned, reusable preference features/cost composition and diagnostics.
2. Implement bounded hill costs with the uncertainty policy above; compare
   reversed and partial traversals, and search returns independently when costs
   are asymmetric, including with zero loop width.
3. Retain crossing evidence and resolve event attribution on these fixtures;
   add complete passage, endpoint, repeated-passage, and unavoidable-route checks.
4. Acquire buffered road/green context, extend this audit with local polygons
   and exposure examples, then select inclusion rules and influence parameters.
5. Review accepted/rejected runs and interaction performance for each preference
   before exposing its UI control.

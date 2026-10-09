# 23. M4 — Path gradients and start-based exploration

Status: M4.1 implemented; M4.2–M4.5 planned, updated 2026-10-09. This is the delivery plan
for M4 in the [map-first roadmap](22-map-first-running-plan.md). No M4 UI or API
implementation is claimed by this document.

## Outcome and scope

Open the map around Ruusutorpan koulu, recognise promising flat corridors or
hills, inspect a path, and see whether it can be included in an outing from a
selected start. Ship the gradient map before adding start-based exploration.

Use the existing Leppävaara/Kilo graph extent for the first delivery. The larger
40 × 40 km terrain map remains available, but path analysis must show its own
coverage boundary. No automatic network downloads occur when panning.

The initial scope is desktop exploration. Responsive controls should remain
usable on smaller screens, but live positioning, navigation, route drawing,
GPX import, complete route generation, and cross-junction hill discovery are
later work. Path inspection does not construct a route.

## Working interaction defaults

These are proposed defaults pending user feedback, not established data facts:

- Show all pedestrian-eligible candidate paths, with surface categories and
  unknowns explicit. Offer a maintained-surface filter without claiming that
  surface tags prove maintenance. Keep stairs visible and marked; an avoid-stairs
  control excludes them from reachability while leaving contextual geometry.
- Click a graph segment to inspect it. Graph segments end at junctions, tagged
  nodes, or OSM way boundaries; a segment is not necessarily a whole named path.
  Longer, manually selected stretches belong to a later interaction increment.
- Start with the existing 40 m terrain smoothing and maximum 10 m gradient
  sampling interval. Path analysis remains fixed at this preset in M4; the
  existing terrain-background slider must clearly indicate that it only changes
  the background. Do not silently mix measurement settings.
- Default to gradient colours, restrained basemap context, and optional terrain
  shading/contours. Start-based highlighting is opt-in. Its initial distance
  budget is 6 km, with editable kilometres rather than a training recommendation.

## What exists and what must change

| Existing component | M4 work |
| --- | --- |
| `paths/graph.py`: directed OSM graph and whole-segment GeoJSON | Preserve analysis coverage/provenance; expose stable IDs; add eligible edge snapping |
| `paths/terrain.py`: projected gradient integration and ascent totals | Retain ordered short-interval measurements and relative terrain profiles |
| `routing/profiles.py`: access/surface/stair classification | Define a shared policy and integrate it into queries and map eligibility |
| `routing/search.py`: forward/reverse distances | Consistent blocked-edge/node handling, temporary snapped start, budgeted queries |
| `api/app.py`: terrain configuration and tiles | Optional path service, bounded feature delivery, inspection, and exploration endpoints |
| `web/src/main.ts`: terrain map and basemap path styling | Path measurement layer, selection panel, start/budget controls, clear status states |

The terrain sampler currently discards individual samples. The prepared terrain
COGs store magnitude and bearing, not smoothed absolute elevation. The map's
basemap paths are a different source from the prepared routing graph. These are
explicit integration gaps, not just styling tasks.

## M4.1 — Preserve inspectable measurements

Extend the existing explicit preparation flow; do not introduce a second OSM
importer. Keep display intervals separate from graph arcs so finer colouring
does not create artificial routing junctions.

For each physical segment, persist ordered interval records containing:

- segment ID, interval index, start/end chainage, and display geometry;
- signed along-path grade in the segment's stored direction;
- estimated height change, validity, and source-quality flags;
- method identity, source manifest, sampling settings, and schema version.

Compute grade as the terrain gradient projected onto the local path tangent.
Show grade as percent, with ascent/descent integrated over interval distance.
Reverse inspection changes sign and order and swaps ascent/descent. Use the
same chainage convention for samples, geometry slicing, and partial-edge
calculations. Record the current distinction between EPSG:3067 sampling lengths
and graph geodesic lengths; map offsets explicitly rather than assuming equality.

The first profile shows **estimated terrain height change from the segment
start**, obtained by integration, not absolute N2000 altitude or surveyed path
elevation. Do not join independently integrated profiles across the network or
force closed-loop elevation consistency. Absolute smoothed-elevation sampling
can be introduced later if the relative profile proves insufficient.

Do not interpolate through missing intervals. Break the chart at gaps and reset
relative height for subsequent valid runs with an explicit label. Partial
ascent/descent summaries carry a valid-length fraction and are not complete
totals. Adjust preparation's current all-or-nothing terrain-coverage check so
partially covered geometry can be inspected without being classified as flat.

Flag bridges, tunnels, stairs, pedestrian-area perimeters, and missing terrain.
Bridge/tunnel ground estimates get a distinct uncertain style, not a confident
flat colour. Their graph connectivity remains usable under access rules.

Keep graph and measurement artifacts separate but tied by a graph digest.
Start with inspectable file-backed data and an in-memory index. Measure size
before choosing a compact storage replacement. Publish atomically and reject
mismatched graph/measurement identities.

Acceptance: flat and tilted planes, a path across a slope, a climb followed by
descent, reversed direction, curved geometry, tile seams, and missing samples
produce consistent interval values, profiles, totals, and quality flags.

### M4.1 implementation notes

The existing `paths prepare` flow now retains short intervals in a separate
content-addressed `measurements-<sha256>.json` file. `graph.json` references its
checksum and byte size and is replaced last as the atomic publication point.
Old measurement generations remain available to readers holding an older graph.
The measurement document includes a canonical graph digest, schema version,
source manifest, and method/settings. `load_measurements(graph_path)` checks
both checksum and graph identity and returns a data-only `MeasurementData`
dataclass with the measurement document and graph lengths indexed by segment ID.
Validation, loading, and inspection are separate functions.

`inspect_measurements(data, segment_id, reverse=False, start_m=0, end_m=None)`
returns ordered intervals, integrated relative profiles, partial ascent/descent,
and valid-length fraction. Inspection ranges use EPSG:3067 chainage; each interval
also records graph offsets derived from cumulative geodesic vertex lengths scaled
to the stored graph length. Reversal preserves original interval IDs, reverses
geometry/order, and changes signs. Profile runs explicitly reset after missing
samples. Bridge/tunnel/stair/area flags remain available even with valid terrain.

Preparation accepts partial terrain coverage and reports measurement file size
and valid-length fraction. Segment/arc totals retain their existing fields for
compatibility; their validity fraction must accompany interpretation. The file
format favors inspection over compactness; representative dataset size and
performance measurements remain part of M4.5. No map style or API is delivered
by M4.1.

Synthetic tests cover flat/tilted planes, cross-slope paths, climb/descent,
reversal, curved geometry, partial intervals, profile gaps, source-quality flags,
tile seams, and graph identity rejection.

## M4.2 — Deliver the gradient map and inspection

Add an optional path service to the existing API. Missing path artifacts leave
the terrain map usable and report path analysis as unavailable. Never prepare
the graph or sample terrain inside a viewport request.

Initial API responsibilities, with route names settled during implementation:

| Request | Response |
| --- | --- |
| Path configuration | Coverage, dataset identity, method/settings, legend, available filters |
| Viewport and zoom | Bounded short-interval GeoJSON with IDs, grade, quality, and surface class |
| Segment ID | Full ordered measurements, relative profile, summaries, tags, and provenance |
| Start, profile, budget, viewport | Snap result and visible interval inclusion states with reasons |

Use a spatial index for viewport lookup. Bound request area and feature count;
an oversized view gets an explicit zoom-in/coverage response, never silently
truncated geometry. Return complete intersecting display intervals initially.
Fine gradient rendering starts at a configured close zoom (initial trial: 14).
Wide views retain terrain and neutral path context until measurements justify
an overview representation that preserves short steep sections.

Keep colours client-side so emphasis/legend changes do not trigger sampling.
Trial magnitude bands are 0–2%, 2–5%, 5–8%, and 8%+, to be reviewed locally.
These are visual categories, not universal definitions of flat or runnable.
The signed value remains available in inspection. Unknown quality has its own
style and never shares the lowest-gradient class.

Selection highlights the whole graph segment and opens a panel with name/type,
length, surface/access evidence, signed grade, relative profile, and ascent/
descent estimates. Hovering the chart highlights the corresponding map interval.
Show uphill arrows on the selected segment only, where grade is reliable and
nonzero. An inspection-direction toggle is independent of permitted traversal.

For climb inspection, group consecutive positive-grade intervals within the
segment and report length, estimated gain, and mean/peak sampled grade. Describe
these as uphill sections, not detected training hills. Do not bridge gaps or
merge across junctions. Defer minimum-climb thresholds and noise-gap merging
until familiar examples expose a need.

Avoid duplicate path drawings: within prepared coverage, the analysis layer
supplies prominent path styling; outside coverage, basemap paths remain context.
Labels and selection must remain legible over optional terrain overlays.
Cancel stale viewport/inspection requests, retain selection by dataset + ID,
and make loading, unavailable, outside-coverage, and failed states distinct.

Acceptance: familiar hills and flat corridors are recognisable; a climb/descent
segment does not appear flat; clicking and reversing inspection gives coherent
map/chart output; panning outside path coverage never implies no paths exist.

## M4.3 — Unify eligibility and start selection

Separate three properties: pedestrian eligibility, surface preference, and
terrain confidence. Missing surface tags are unknown, not proof of a poor or
maintained path. Low terrain confidence does not remove a legal connection.

Use one normalized access/profile policy for display eligibility, start snapping,
forward/reverse reachability, and baseline routing. Preserve pedestrian overrides
of generic access and explicit pedestrian directions. Restricted tagged nodes
block traversal and cannot be bypassed by snapping or by a destination exception.
Integrate retained profile helpers rather than duplicating their rules in the UI.
Keep the baseline's experimental cost/ranking settings separate from eligibility.

Audit importer exclusions before describing the map as complete. Paths rejected
during import cannot be recovered by a UI filter. In particular, document the
current cycleway permission rule and pedestrian-area perimeter approximation.
Do not broaden access rules implicitly as part of visualization.

In set-start mode, snap to the nearest eligible physical segment, with a temporary
split node and directed partial arcs. Do not permanently rewrite the prepared
graph. Display requested and snapped points plus offset; the visual offset is
not a validated access route. Begin with a configurable 50 m maximum snap radius.
Reject distant snaps and allow choosing another nearby path where parallel or
grade-separated candidates are ambiguous. Do not snap automatically to a larger
component merely because it produces more possibilities.

Acceptance: starts inside an edge work in both legal directions; one-way edges,
restricted gates/endpoints, parallel paths, and bridge crossings preserve the
same eligibility and connectivity in inspection and search.

## M4.4 — Show outing inclusion within a distance budget

From the snapped start, run distance-only Dijkstra on the filtered directed graph
and its reverse. Distances are metres, independent of grade colours or a
flatness emphasis setting. Bound exploration by the requested budget.

For a complete directed edge u -> v of length L:

```text
minimum inclusion distance = distance(start, u) + L + distance(v, start)
```

For a short displayed interval, use its actual endpoints and legal partial arcs.
On a two-way segment an outing can turn within the segment; using only the
whole-edge formula would hide reachable portions of long paths and dead ends.
Evaluate interval traversal in each legal direction with partial-edge access and
return distances, or equivalent virtual splits. Avoid per-interval graph-wide
searches. Missing outward or return distance makes that traversal unavailable.

Define the highlight as: **this entire displayed interval can be traversed on
some outing within the budget**. It does not mean every highlighted path can be
visited in a single outing. A threshold falling inside a display interval may
leave it conservatively unhighlighted; retain the numerical value for inspection
and document resolution. Reachability is calculated over the whole prepared
graph, not just the current viewport.

Keep gradient hue; use emphasis/opacity to separate included paths, paths beyond
the budget, and paths with no return connection in the extract. Surface exclusions
and unknown coverage need distinct explanations. A flatter-path control only
emphasises gentler intervals, retaining hill connectors as context.

Label results “Within a 6 km outing — retracing allowed”, with the actual selected
budget. A bridge or dead end may require an out-and-back traversal. Do not claim
a generated route, exact target length, or no-repetition loop.

Prepared coverage can omit a shorter connection or return path. Flag results near
the extract boundary as coverage-limited and say “no connection in prepared data”
instead of “impossible”. Broader coverage is a later preparation decision.

Cache bounded distance indexes by dataset, eligibility policy, snapped edge and
offset, and search limit. A larger cached limit may serve smaller budgets; a
smaller one must not imply unreachable results for a larger request. Viewport
changes reuse distance results. Profile/start changes invalidate them; display
colour changes do not. Prevent stale responses from replacing newer selections.

Acceptance: a stem to a loop, pure dead end, one-way outward-only branch,
disconnected component, restricted gate, partial long edge, and exact-budget
boundary have analytically checked inclusion distances. Increasing the budget
cannot reduce inclusion under the same graph and profile.

## M4.5 — Local review and performance gate

Create a small reproducible fixture list with coordinates/OSM IDs and expected
observations: a familiar flat corridor, short rise, sustained hill, rolling path,
bridge/underpass, stair connection, missing-surface path, restricted connection,
and extract-edge case. User-selected locations improve this list, but synthetic
checks and first local inspection can proceed without them.

Record whether each example is recognisable, whether uncertainty is clear, and
whether path quality or source geometry is misleading. Track data issues
separately from numerical/UI defects; fix the failures that block the examples.

Measure preparation time and artifact size, service startup/index memory,
viewport response bytes, cold/warm query latency, browser render responsiveness,
and start/profile/budget changes. Record machine, extent, zoom, and settings.
Set numerical performance targets after the first representative measurement.
Use compression, spatial indexing, and bounded responses first; adopt vector
tiles or alternate storage only if these measurements justify the added work.

Run numerical/graph/API tests and the frontend typecheck/build. Manually verify
chart/map linking, controls, request races, legends, and coverage states. M4 is
complete when the fixture review and measured interaction performance support
the exploration outcome, with remaining limitations documented.

## Implementation order and decision gates

1. M4.1: persist measurements and prove their meaning on synthetic examples.
2. M4.2: ship a local gradient map with click inspection; review visual usefulness.
3. M4.3: apply consistent eligibility and add explicit start selection.
4. M4.4: add budgeted inclusion with partial-edge handling and clear semantics.
5. M4.5: complete local review and performance measurements.

Do not begin route generation to compensate for an unclear exploration map.
If the local review needs longer inspection stretches or absolute elevation,
scope that increment explicitly before expanding the graph or adding routing UI.

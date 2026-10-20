# First UI-driven run planner

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

Status: first map feedback version implemented, 2026-10-14; tuning tools and
broader local review remain. First application slice of M7, using the
[smooth loop experiment](26-smooth-loop-routing-experiment.md). Prioritize a
usable map feedback tool before running-time calibration or automatic suggestions.
This slice can proceed alongside the remaining M4 local review.

The first feature set is accepted for further UI work. Follow-up plans cover
[UI layout and settings ownership](28-planner-ui-and-settings.md) and
[routing preferences](29-routing-preferences.md). The UI plan refines the layout
below; the preference plan extends the cost model without requiring running-time
calibration first.
The UI layout checkpoint is now implemented: tab navigation, separate settings,
compact legends, persistent planner actions, and a mobile bottom sheet. See the
UI plan for verification and remaining preference integration.

## First implementation

The map now has Explore / Plan run modes, shared home and path policy, temporary
endpoint picking, snap offsets, midpoint/dividing-line and avoidance-circle
guides, explicit generation/cancellation, candidate switching and Fit run.
Exploration controls and reachability shading are hidden while planning; path
inspection remains available. Results survive mode switches, and changing
settings marks the displayed run as out of date.

`POST /api/paths/run` constructs both side assignments with distance costs on
the prepared network. It splits both endpoints together (including same-segment
snaps), preserves directed eligibility, integrates over polylines, and returns
partial physical traversal references and actual distances separately from cost.
Field integrals are reused within a request; reuse across requests remains a
performance follow-up. A* uses a projected-distance bound calibrated against all
arc lengths. Work runs in synchronous API workers, with two concurrent planners
and a configurable `RUNMAP_RUN_DEADLINE` (initially 15 seconds). Completed
candidates survive expiry of the second construction.

Cards show per-leg/total distance, repeated distance counted after the first
traversal, closest centre approach and enclosure for simple closed geometry.
Retracing, crossings or a centre on the path get an unclear-enclosure state.
Equivalent route geometry is labelled, including opposite traversal of the same
loop. Coverage limitations accompany results. Running time and ascent are not
yet shown in the baseline version. The later [hill preference](32-hill-routing.md)
adds estimated ascent/descent and explicit uncertainty when active; running time
remains unavailable.

Initial local smoke test: the 55,627-node Leppävaara dataset loaded in 4.72 s;
both assignments for home (24.795, 60.216) and turnaround (24.815, 60.225)
completed in 0.46–0.51 s and produced a 5.27 km run with zero repeated distance.
This is one example, not a performance guarantee. The Python suite has 135
passing tests; frontend typecheck/build and desktop interaction checks pass.

Advanced settings, field/repetition overlays, copied feedback
records, cross-request integral caching, and broader fixture review remain.
This version is ready for the first map feedback checkpoint.

## Outcome

Select home and a turnaround on the existing map, generate a run returning home,
and adjust how widely it goes around their midpoint. Compare both side
assignments and inspect where the network permits or prevents the intended
shape. A complete route is useful feedback even when it cannot enclose the centre.

Use the existing prepared network and distance as the base cost. Preserve
pedestrian access, direction, surface, stairs, and prepared-boundary rules.
The existing outing-inclusion distance budget does not constrain this planner;
label the two tools separately. Target length/time, automatic turnaround choice,
speed models, navigation, GPX export, and editable exclusion areas are later work.

## Map interaction

Use “Run” in user-facing planner labels: “Plan run” and “Generate run”. Name
candidates “Clockwise” and “Counter-clockwise” for their opposite side-planning
preferences, or “Out and back” when the return exactly retraces the outbound.
Route and candidate remain useful technical terms.

Separate the sidebar into explicit “Explore” and “Plan run” modes. Share the
map, selected home, and a “Path filters” section for surface and stair policy.
Explore retains gradient/distance colouring, reachability and its distance
budget. Plan run replaces those controls with endpoints, avoidance and candidate
results; hide reachability shading and keep gradient colouring available as
subdued context beneath the route lines.

Preserve each mode's settings and results when switching. Hide planner overlays
in Explore and restore them in Plan run. Shared filter changes mark an existing
run out of date. Keep path inspection available beneath the active tool in both
modes. Endpoint picking is temporary and has an explicit instruction and Cancel
action; ordinary clicks inspect paths.

The “Plan run” panel follows this sequence:

1. Set home, reusing the existing selected start when available.
2. Set turnaround through an explicit map-picking mode. Outside picking modes,
   clicks retain path inspection. Allow replacing or clearing either endpoint.
3. Show requested and snapped locations, snap connectors, midpoint, connecting
   line, and the avoidance-distance circle. Report failed snaps next to the
   affected endpoint. Keep the existing snap-radius and eligibility controls.
4. Press “Generate run”. Show progress and allow cancellation. Keep the last
   completed result visible while recomputing, marked as belonging to the
   previous settings. Endpoint or policy changes mark it out of date immediately.
5. Compare at most two candidate cards labelled “Clockwise” and
   “Counter-clockwise” (or “Out and back” for exact retracing); select a
   card to emphasize its route.
   Use one blue line for the whole run, start/turnaround markers, and sparse
   arrows following traversal direction. Keep route lines legible over gradient colouring;
   provide a “Fit run” action without resetting the view on every response.
6. Adjust avoidance distance and regenerate. Initially use an explicit button
   rather than issuing searches continuously while dragging a slider.

Show avoidance distance in metres and as a fraction of endpoint separation.
Explain it as a soft cost contour, not a minimum clearance. Include a separate
“Centre avoidance off” setting; zero distance otherwise corresponds to strength
one in the mathematical field model. In the application, zero avoidance is an
explicit shortest out-and-back mode: disable centre, side and reuse penalties,
return one candidate, and retrace the outbound path when legal. If pedestrian
directions prevent retracing, find the shortest legal return instead. The
centre-only checkbox leaves side and reuse preferences active at nonzero values.
All three preferences now ramp continuously from zero to their full values at
18% avoidance. For slider fraction `p`, use `t = min(1, p / 0.18)` and
`ramp = t² * (3 - 2t)`. Effective centre strength is
`ramp * exp(p² / (2 * radius_ratio²))`; side and reuse strengths are their
configured values multiplied by `ramp`. This preserves defaults and the former
mapping at 18% and above, with zero slope at both ends of the ramp. At 1%, the
default effective strengths are approximately 0.00894, 0.0178 and 0.0357.
Below 18%, the displayed avoidance distance is a tuning scale rather than the
exact equal-base-cost contour because the ramp attenuates the field. Route
choices can still change discretely as the network's preferred corridor changes.
Use the experiment's 0.18 ratio only as a provisional starting value.
Set Gaussian scale proportionally to endpoint separation for the initial UI
(12/80 = 0.15), display its value, and keep it fixed while avoidance is adjusted.

An “Advanced” section exposes Gaussian scale, side preference, reuse penalty,
and side-transition width with units and a reset action. Start from side=2,
reuse=4, and transition width 4/80 of endpoint separation. These are trial
settings; record their actual values in every result. Validate finite bounded
inputs and cap the derived exponential before scheduling a search. Establish
the supported ranges during the real-network slice, rather than promising
arbitrary strengths.

## Results and feedback visuals

For each completed candidate show total and per-leg distance, repeated physical
distance, closest centre approach, and whether the closed walk encloses the
centre. Handle self-intersections and a centre on the route explicitly; winding
alone does not establish a simple loop or avoidance of the whole disk.
Show estimated ascent only with terrain coverage/quality and do not display a
predicted running time yet. Artificial penalties never contribute to distance.

Provide optional overlays for the centre field and repeated segments. A compact
diagnostic section shows penalty contributions, preparation/search duration,
expanded nodes, dataset identity, settings, and coverage warnings. Keep search
wavefront animation in the standalone experiment initially.

Distinguish failed snapping, coincident snapped endpoints, unreachable legs,
deadline expiry, unavailable data, and server failure. On expiry retain any
completed candidate. A finished run that does not enclose the centre remains
selectable with a clear shape warning. Identify equivalent candidates rather
than presenting duplicate routes as meaningful alternatives.

Add “Copy experiment details” to copy a reproducible JSON record of endpoints,
snaps, policy, dataset identity, settings, candidate metrics and outcomes.
This plus a map screenshot supports user feedback without a persistence service.

## Implementation slices

### 1. Production routing adapter

Extract reusable field geometry and integration functions from the experiment;
keep synthetic graph construction, plotting and tracing separate. Use data-only
settings/results containers and module functions. Extend the production search's
existing heuristic hook with an explicit arc-cost provider and bounded-search
outcome, preserving existing callers.

Reuse policy filtering and snapping from path exploration. Construct one
temporary overlay for both snapped endpoints, splitting affected segments in
chainage order, including two snaps on the same segment. Preserve legal
directions, partial lengths, geometry, and physical-segment identity without
mutating the loaded graph. Reuse penalties must apply to overlapping physical
chainage intervals; two disjoint portions of one segment are not repeated path.

Integrate spatial penalties along projected EPSG:3067 polyline geometry with
bounded-spacing quadrature. Keep graph distance and projected integration units
explicit. Cache field integrals in a bounded cache keyed by dataset, endpoint
geometry, Gaussian scale and transition width; strength changes only reweight
them. Handle partial arcs consistently. Use an admissible distance heuristic
in the base-cost metric; verify projection bounds or fall back to zero.

Construct outbound-left/return-right and outbound-right/return-left with soft
reuse penalties. Validate closed directed walks and calculate diagnostics from
their actual geometry. Bound all preparation and searches by a shared deadline;
expiry is not evidence of disconnection. No automatic retry loop.

Acceptance: real prepared-network routes are valid and reproducible; same-edge
snaps, directed paths, unavoidable stems, disconnected endpoints, and zero
penalties behave correctly. Geometry splitting preserves integrated cost within
numerical tolerance. Measure cold preparation and warm candidate construction.

### 2. API and first usable map tool

Add a bounded `POST /api/paths/run` request to the existing optional path
service. Accept both endpoint selections, policy, settings, and dataset identity;
return resolved snaps, candidate leg GeoJSON, physical traversal references,
metrics, per-candidate outcomes, and timing. Reject stale dataset identities.
Advertise defaults, limits, and availability in path configuration.

Run CPU work outside the async request loop with bounded concurrency. A server
deadline must stop work even after browser cancellation; cancelling a browser
request alone does not stop a Python search. Bound request/response size and
cache memory. Panning must not change the graph used for routing.

Implement the Explore/Plan run modes, picking modes, generation button, candidate switch, avoidance
control, route overlays and clear status states in `web/src/paths.ts` and the
existing map UI. Cancel obsolete requests and use response sequence checks so
older results cannot overwrite newer inputs. Reuse existing path inspection.
During a request, the Generate run button reads “Generating run…”, is disabled,
and exposes `aria-busy`; restore it on completion, failure or cancellation so
loading remains visible even when the detailed status is below the scroll fold.

Acceptance: select two points, generate, compare, change avoidance, and regenerate
without leaving the map. This is the first feedback checkpoint; do not wait for
advanced overlays or time modelling before local review.

### 3. Feedback and tuning tools

Add advanced settings, centre-field and repeated-path overlays, enclosure
diagnostics, and copied experiment records. Review familiar open corridors,
bridges/underpasses, shared access stems, sparse networks, and extract edges.
Save a small fixture list from user feedback and adjust defaults from those
examples. Keep data problems separate from algorithm and interaction defects.

Acceptance: the user can explain why a route changes and report an undesirable
run reproducibly. All outcomes remain inspectable when the network defeats
the intended enclosure.

## Verification and completion

Use focused graph/cost/geometry tests, API validation/deadline tests, frontend
typecheck/build, and browser checks for picking, stale responses, candidate
switching, policy changes and narrow screens. Verify copied settings reproduce
the route against the same dataset. Avoid tests that only mirror implementation.

Record machine, dataset, cold/warm latency, response size and browser usability.
Use the experiment's one-second warm target for both candidates as an initial
measurement goal. Set a configurable server deadline and report misses; choose
its initial value after the real-network measurements.

Complete this slice when a runner can generate and inspect runs on the map,
tune avoidance, recognize constrained or incomplete results, and provide
reproducible feedback. Running-time costs follow as a separate increment using
the same planner interface.

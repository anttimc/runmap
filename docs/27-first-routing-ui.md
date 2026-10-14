# First UI-driven outing planner

Status: planned, 2026-10-14. First application slice of M7, using the
[smooth loop experiment](26-smooth-loop-routing-experiment.md). Prioritize a
usable map feedback tool before running-time calibration or automatic suggestions.
This slice can proceed alongside the remaining M4 local review.

## Outcome

Select home and a turnaround on the existing map, generate a closed outing,
and adjust how widely it goes around their midpoint. Compare both side
assignments and inspect where the network permits or prevents the intended
shape. A complete route is useful feedback even when it cannot enclose the centre.

Use the existing prepared network and distance as the base cost. Preserve
pedestrian access, direction, surface, stairs, and prepared-boundary rules.
The existing outing-inclusion distance budget does not constrain this planner;
label the two tools separately. Target length/time, automatic turnaround choice,
speed models, navigation, GPX export, and editable exclusion areas are later work.

## Map interaction

Add an opt-in “Plan outing” panel with this sequence:

1. Set home, reusing the existing selected start when available.
2. Set turnaround through an explicit map-picking mode. Outside picking modes,
   clicks retain path inspection. Allow replacing or clearing either endpoint.
3. Show requested and snapped locations, snap connectors, midpoint, connecting
   line, and the avoidance-distance circle. Report failed snaps next to the
   affected endpoint. Keep the existing snap-radius and eligibility controls.
4. Press “Generate outing”. Show progress and allow cancellation. Keep the last
   completed result visible while recomputing, marked as belonging to the
   previous settings. Endpoint or policy changes mark it out of date immediately.
5. Compare at most two candidate cards; select a card to emphasize its route.
   Use orange outbound and blue return lines, start/turnaround markers, and
   sparse direction arrows. Keep route lines legible over gradient colouring;
   provide a “Fit route” action without resetting the view on every response.
6. Adjust avoidance distance and regenerate. Initially use an explicit button
   rather than issuing searches continuously while dragging a slider.

Show avoidance distance in metres and as a fraction of endpoint separation.
Explain it as a soft cost contour, not a minimum clearance. Include a separate
“Centre avoidance off” setting; zero distance otherwise corresponds to strength
one. Use the experiment's 0.18 ratio only as a provisional starting value.
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
completed candidate. A finished outing that does not enclose the centre remains
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

Add a bounded `POST /api/paths/outing` request to the existing optional path
service. Accept both endpoint selections, policy, settings, and dataset identity;
return resolved snaps, candidate leg GeoJSON, physical traversal references,
metrics, per-candidate outcomes, and timing. Reject stale dataset identities.
Advertise defaults, limits, and availability in path configuration.

Run CPU work outside the async request loop with bounded concurrency. A server
deadline must stop work even after browser cancellation; cancelling a browser
request alone does not stop a Python search. Bound request/response size and
cache memory. Panning must not change the graph used for routing.

Implement the picking modes, generation button, candidate switch, avoidance
control, route overlays and clear status states in `web/src/paths.ts` and the
existing map UI. Cancel obsolete requests and use response sequence checks so
older results cannot overwrite newer inputs. Reuse existing path inspection.

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
outing reproducibly. All outcomes remain inspectable when the network defeats
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

Complete this slice when a runner can generate and inspect outings on the map,
tune avoidance, recognize constrained or incomplete results, and provide
reproducible feedback. Running-time costs follow as a separate increment using
the same planner interface.

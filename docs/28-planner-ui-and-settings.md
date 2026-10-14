# Planner UI and settings

Status: UI checkpoint implemented, 2026-10-14; preference controls follow their
routing support. Follow-up to the accepted first version of the
[run planner](27-first-routing-ui.md). Routing preferences have a separate
[implementation plan](29-routing-preferences.md).

## Implementation and review

Slices 1–3 are implemented with the existing frontend stack. Explore / Plan run
are keyboard-accessible tabs. Path settings and Map settings use a native modal
dialog styled as a desktop drawer or mobile sheet, with Escape, focus return,
and backdrop dismissal. Endpoint snapping sits under shared advanced settings;
gradient emphasis stays in Explore. Algorithm tuning remains a later planner
tool; no unsupported preference controls are shown.

The Explore control is now “Highlight flat paths”, with a maximum absolute grade
slider from 1–10% in 0.5% steps, initially 3%. Eligible measured intervals at or
below the threshold are highlighted only when part of a continuous flat stretch
of at least 50 m within one mapped segment. Highlighted intervals use 80% opacity
and are 1.5 times wider. Other intervals retain their normal gradient colours,
opacity, and width; highlighting adds emphasis without suppressing the terrain
context. Uncertain/missing grades retain their normal dashed styling and are
never classified as flat. Existing eligibility/reachability fading still applies.
Turning highlighting off restores normal gradient rendering. The legend names
the active threshold. Highlighting is display-only, stays out of Plan run, and
restores when returning to Explore.

The length test uses projected sample chainage before viewport selection, tile
clipping, simplification, or grade bins. At dataset load, record the lowest
supported slider threshold where each interval belongs to a qualifying stretch;
tiles carry that scalar so slider changes stay client-side. Missing terrain,
bridge/tunnel estimates, stairs, and chainage gaps break stretches. Do not bridge
short steep interruptions. Segment boundaries also break stretches: joining
across junctions is a later refinement if local feedback warrants it. Tile render
version 2 invalidates cached tiles without this metadata. Colour bins remain
zoom-dependent, but flat qualification uses original grades.

Browser paint checks verified the inclusive threshold in both directions,
short-patch exclusion, softer opacity, uncertain and excluded intervals,
threshold updates without backend requests, mode restoration, and normal
rendering after switching the control off. The 147-test Python suite covers
length boundaries, signed grades, grade-dependent joining, missing/uncertain
samples, tile metadata and coalescing. Frontend typecheck/build passes.

Plan run has compact endpoint rows, Loop width, optional shape help, selectable
run summaries, expandable diagnostics, and a fixed Generate/status/cancel footer.
Mobile uses an expandable bottom sheet whose compact state retains the planner
action and status. Mode switches preserve result status as well as geometry;
changing the shared home in Explore invalidates a run when returning to planning.
Display-only settings leave the run valid.

Legends use compact horizontal ramps with 0/10/20/40% labels, optional symbol
details, and inline Out/Back keys. Path keys hide below supported zoom or outside
prepared coverage; the terrain key hides at zero opacity. Fit run accounts for
the panel/sheet and legend. Mobile inspection appears above the sheet and hides
the legend while open.

Verification: frontend typecheck and production build pass (the existing large
bundle warning remains). Browser checks at 1280 × 900 and 390 × 844 covered
generation, stale results, geometry restoration, keyboard tabs, settings focus,
display-only changes, picking cancellation, mobile collapse/expand, and Fit run.
The local 5.27 km fixture produced both candidates in 0.47–0.66 s in these checks.
Additional mobile checks covered loading/cancel, a simulated request failure,
shared policy invalidation, and real path inspection above the sheet. The compact
legend measured about 150 px high with both ramps visible. These are local smoke
checks, not a guarantee across all devices; user feedback is the next checkpoint.

## Outcome and scope

Make Explore and Plan run clearly distinct, keep the primary workflow visible,
and reduce the space taken by settings and legends. Preserve current routing
behaviour during this change. Accommodate future preferences without presenting
controls that do not yet affect routes.

Keep TypeScript, Vite, and MapLibre. Use native HTML controls and focused modules
for state and rendering. A frontend framework migration is outside this work;
reconsider only if concrete state-management or component-reuse problems remain
after the layout is simplified.

## Navigation and layout

Place prominent Explore / Plan run tabs at the top of the tool panel. Implement
tab semantics, associated panels, selected state, visible keyboard focus, and
arrow-key navigation. Mode switching preserves settings, endpoints, results,
and map position. Path inspection remains available in both modes.

Desktop layout: a bounded panel with fixed tabs and a fixed primary-action
footer in Plan run. Only the content between them scrolls when necessary. Keep
ordinary settings short enough that scrolling is exceptional at typical laptop
sizes. Avoid nested scrolling regions inside the task panel.

Mobile layout: an expandable bottom sheet with compact and expanded states.
The compact state shows the active mode, essential status, and planner action;
the expanded state exposes settings and results. Keep map controls and endpoint
picking instructions reachable, and account for device safe-area insets.

Detailed settings open in one secondary settings surface: a drawer on desktop
and a sheet on narrow screens. Group settings there rather than adding several
layers of accordions. Return focus to the opener on close. Modal presentations
must support Escape and appropriate focus management.

## Setting ownership

| Scope | Settings | Effect |
| --- | --- | --- |
| Explore | Gradient/distance view, distance budget, gradient emphasis | Exploration calculations or presentation only |
| Plan run | Home, turnaround, loop width, centre toggle, future preferences | Run construction only |
| Shared path settings | Allowed surfaces, avoid stairs | Eligibility for exploration and planning |
| Map settings | Terrain smoothing/opacity, contours, building-access visibility | Presentation only |
| Advanced endpoint settings | Maximum snap offset | Endpoint resolution for both modes |
| Advanced planner settings | Field scale, side/reuse strength, transition width | Run construction only |

Keep the selected Explore start and planner home shared as in the current UI;
the turnaround belongs to Plan run. Clearly identify shared settings with
“Applies to both modes.” Changing surface or stairs policy updates exploration
and marks an existing run out of date. Advanced snap changes revalidate endpoints
and invalidate affected calculations. Display-only changes do not invalidate a
run. Audit existing visibility/filter wiring before moving controls: the new
labels must match actual effects, especially building-access visibility.

Separate maximum snap offset from algorithm tuning even though both are
advanced. Terrain smoothing changes the background, not prepared path grades.
Preserve that distinction in the settings help.

## Plan run workflow

1. Compact Home and Turnaround rows show selection state and a Change action.
   Picking has an explicit instruction and Cancel; ordinary clicks inspect.
2. Run shape contains the current avoidance slider and centre toggle. Trial
   label: “Loop width”, with metres and percentage retained. Help explains the
   midpoint preference and that it is not guaranteed clearance. Keep the current
   smooth ramp and zero-avoidance behaviour.
3. Route preferences occupy a compact section when implemented. Use the same
   Off / Low / Medium / High interaction for hills, traffic lights, busy roads,
   and greenery. Each maps to its own calibrated cost, not a common raw weight.
   Do not add enabled placeholders during the UI-only increment.
4. Results show selectable Run A / Run B summaries with total distance and
   repeated distance. Expand a selected run for per-leg and diagnostic details.
   Keep equivalent-route and incomplete-result states visible.
5. A fixed footer contains Generate run, its waiting state, and Cancel while
   generating. Keep a concise stale/error/status message nearby; detailed
   explanations can remain with the results. Fit run belongs with the result.

Keep the last completed run visible while regenerating and identify stale
results. Preserve request cancellation and sequence protection. The footer
must never cover the final settings or result rows.

In the future, zero loop width disables shape penalties only. Once other
preferences exist, it means out-and-back on the preferred route, rather than
necessarily the shortest-distance route. Update help when that feature lands.

## Compact legends

Render legend content from visible layers and active metric, independently of
the active tab. Do not reserve space for hidden layers.

- Gradient paths: one horizontal ramp with a small number of labelled ticks.
- Distance paths: one horizontal distance ramp with current units/range.
- Planned run: inline orange Out / blue Back keys beside the result.
- Terrain shading: a compact separate ramp only while that shading is visible.

Retain a small gradient key if gradient paths are visible beneath a planned run.
Make exceptional path symbols and fuller explanations available through a
legend-details control. Keep prominent warnings such as missing data on an
inspected route in the inspection/result itself. The compact legend must remain
understandable without relying on colour alone.

## Delivery slices

### 1. Tabs and settings ownership

Refactor mode navigation and group controls by their actual effects. Establish
explicit mode, display, eligibility, and planner state boundaries in the frontend
without changing API contracts or introducing a general state framework.

Acceptance: switching tabs preserves work; shared policy applies consistently;
presentation changes leave computed results valid; keyboard navigation works.

### 2. Planner panel and responsive layout

Implement compact endpoint rows, shape controls, result summaries, fixed action
footer, and the secondary settings surface. Add the mobile bottom-sheet layout.
Move long help text into contextual details. Keep common actions direct.

Acceptance: select endpoints, generate, compare, adjust, cancel, and inspect on
desktop and mobile without searching a long scrolling panel for the action or
status. Results and inspection do not overlap essential controls.

### 3. Contextual legends and map settings

Move presentation controls to Map settings and replace large legends with
compact contextual keys. Adjust Fit run padding for the actual panel/sheet
bounds and use the same bounds for inspection placement.

Acceptance: every visible encoding has an accessible key; hidden layers consume
no legend space; fitted runs remain visible beside the panel and above the sheet.

### 4. Preference controls as routing support arrives

Add each preference through the shared interaction defined above, using API
capability/default information. Explain unavailable data and disable unsupported
options. Preference changes mark a result stale; opening settings does not.

## Verification and feedback

Run the frontend typecheck/build and review browser interactions on a laptop
viewport and a narrow touch viewport. Check keyboard tabs, settings focus,
endpoint picking/cancel, generation failure/cancel, stale results, mode
restoration, inspection, and Fit run. Check long statuses and expanded results.
Use focused state tests only for nontrivial behaviour introduced by the refactor.

Capture before/after screenshots for user feedback. Confirm that the primary
planner action stays visible, the map has more usable area, and routine use
requires fewer scrolls. Do not require preference algorithms to complete this
UI checkpoint.

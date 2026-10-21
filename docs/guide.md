# Using Runmap

Runmap has Explore and Plan run tabs sharing the same map, home/start location,
surface policy, and stair policy. Path inspection works in both tabs. Switching
tabs preserves settings and results; changing routing inputs marks an existing
run out of date. Map settings change presentation only.

## Explore

Gradient view colors prepared path intervals by absolute along-path grade.
Click a path to inspect signed grades, relative height change, estimated ascent
and descent, surface/access tags, and segment-local uphill sections. Hover or
focus the profile to locate its interval on the map. Reverse inspection changes
the displayed direction, not legal traversal permissions.

The profile integrates sampled terrain gradients. It is relative to the segment
start, not absolute altitude or surveyed path elevation. Missing samples break
the profile into separate runs. Bridges and tunnels use uncertain ground-surface
estimates; stairs and pedestrian-area perimeters retain their quality warnings.

“Highlight flat paths” emphasizes measured stretches of at least 50 m within
one graph segment. The maximum absolute-grade threshold starts at 3%, ranges
from 1–10% in 0.5% steps, and applies in either direction. Missing measurements,
bridges, tunnels, stairs, and gaps break stretches. Highlighting affects display
only; it does not remove hill connections or change routing costs.

### Distance from start

Select Distance from start and choose a location on the map. Snapping chooses
the nearest eligible prepared segment; inspection can offer nearby alternatives.
The connector shows the snap offset, not a validated access path.

The budget defaults to **3 km of forward network distance**. Full emphasis means
an entire measured interval can be traversed from the snapped start within that
budget. Return distance is excluded, and a return connection is not guaranteed.
The highlighted intervals need not all fit into one run. An interval crossing
the budget boundary may remain conservatively unhighlighted.

Colors show shortest legal outward distance to interval midpoints in 0.5 km
bands across the selected budget. Gray means beyond budget, excluded,
disconnected, or unavailable; it never means zero distance. Clearing the start
restores gradient view.

Known wording issue: a generated status label still says “outing — retracing
allowed.” Interpret the tool using the forward-budget definition above; the
label correction is tracked in the [roadmap](roadmap.md).

## Plan run

1. Click the map to set home; turnaround selection starts automatically. Click
   again to set a distinct turnaround. Drag either marker to adjust its snapped position.
2. Adjust Loop width with the slider or by dragging the circle edge or its handle
   inward or outward. Its centre stays at the endpoint midpoint. Adjust Avoid hills
   where supported. Generate again after moving markers or changing settings.
3. Select Generate run. Compare the candidate cards and use Fit run to inspect
   the result. Cancel stops waiting in the browser; server work has its own deadline.

Nonzero Loop width produces up to two candidates using opposite side
preferences around the midpoint. Centre, side, and reuse preferences are soft:
crossing the centre, retracing, and equivalent candidates remain possible.
Increasing width does not guarantee a longer run or enclosure of the midpoint.
Zero width disables shape penalties and produces one candidate; hill avoidance
remains active. The Explore distance budget does not constrain this planner.

Avoid hills offers **Off / Low / High** when prepared measurements are available.
It penalizes terrain-derived excess effort, with an additional preference for
gentler climbs. Flat ground and moderate descents add no hill penalty. Missing,
bridge, and tunnel measurements use a conservative fallback reported separately.
The weights and formula are in the [routing reference](reference/routing-mathematics.md).
Traffic-light, busy-road, and greenery preferences are not implemented.

Results distinguish actual distance and repeated distance from artificial cost.
Hill-enabled results include estimated ascent/descent and terrain coverage;
Off results leave terrain observations unavailable. Identical physical candidates
are marked equivalent. Completed candidates can survive the second candidate's
deadline expiry. Running time, fixed target distance, editable waypoints, retained
sections, and GPX export are not available.

## Shared settings and coverage

Path settings offer all prepared candidates or explicitly paved/gravel surfaces,
with unknown surface tags kept explicit. Stairs are avoided by default. Access,
pedestrian direction, and prepared route boundaries govern inspection eligibility,
snapping, exploration, and planning. Display filters cannot restore paths rejected
during import. The default snap radius is 50 m, configurable up to 200 m.

Map settings can hide conservatively identified building-access dead ends.
This affects colored geometry only: legal snapping and routing still use those
branches. The separate basemap may continue to show them faintly.

The terrain region is a 40 × 40 km square near Leppävaara. The prepared path
dataset covers a smaller Leppävaara/Kilo area and shows its own boundary. Panning
does not download more analysis data. Outside it, basemap paths are context.
Missing path artifacts leave the terrain map available; corrupt artifacts report
a failed path service. Missing connections mean no connection in prepared data,
not proof that none exists on the ground.

Terrain background smoothing and opacity do not change prepared path measurements.
The named-region background offers raw and 20–100 m smoothing presets, initially
40 m. Source-elevation contours occur every 5 m, with 25 m contours emphasized;
they show terrain shape but have no elevation labels to resolve uphill sign.
Path tiles appear from zoom 10 through 16 and overzoom thereafter; coarse colors
summarize intervals, while inspection retains original measurements.

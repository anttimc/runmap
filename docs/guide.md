# Using Runmap

Runmap uses one map with a compact **Plan run** panel, independent **Map layers**
controls, and a **Settings** dialog. The planner and Distance from start share
the home/start location and routing policies. Changing routing inputs marks an
existing run out of date. Map appearance changes affect presentation only.

## Map layers

The Map layers panel has separate switches for **Background gradient**, **Path
gradient**, and **Distance from start**. They can be displayed together and remain
available while planning a run. Background gradient starts on; the other two
layers start off. Expand a layer heading to access its options. On narrow
screens, the Map layers panel starts collapsed.

Background gradient colors terrain slope using fixed 40 m smoothing. Its options
include background opacity, initially 75%, and Highlight flat paths. Terrain
colors are softened while distance shading is on.

Path gradient colors prepared path intervals by absolute along-path grade.
Click a path to inspect signed grades, relative height change, estimated ascent
and descent, surface/access tags, and segment-local uphill sections. Hover or
focus the profile to locate its interval on the map. Reverse inspection changes
the displayed direction, not legal traversal permissions.

The profile integrates sampled terrain gradients. It is relative to the segment
start, not absolute altitude or surveyed path elevation. Missing samples break
the profile into separate runs. Bridges and tunnels use uncertain ground-surface
estimates; stairs and pedestrian-area perimeters retain their quality warnings.

**Highlight flat paths**, under Background gradient, emphasizes measured stretches
of at least 50 m within one graph segment. The maximum absolute-grade threshold
starts at 3%, ranges from 1–10% in 0.5% steps, and applies in either direction.
Missing measurements, bridges, tunnels, stairs, and gaps break stretches. Highlighting affects display
only; it does not remove hill connections or change routing costs. It is active
while Background gradient is on, independently of the Path gradient switch.

### Distance from start

Turn on Distance from start. If no start exists, selection begins automatically;
otherwise it uses the shared start. Its options include Set start on map and
Clear start. Snapping chooses the nearest eligible prepared segment; inspection
can offer nearby alternatives. The connector shows the snap offset, not a
validated access path.

The budget defaults to **3 km of forward network distance**. Return distance is
excluded, and a return connection is not guaranteed. Under Advanced options,
choose Grid or Filtered triangulation to display filled 0.5 km distance bands
over the basemap; the final band can be shorter. The method selector reports
generation time.

The bands interpolate shortest legal outward distances sampled along the network.
Filled ground between paths is approximate and does not establish that it can be
traversed. Both methods clip to prepared coverage but have no water or barrier
polygons. Unfilled areas do not establish why distance is unavailable.
See [distance shells](distance-shells.md) for approximation and method details.

Distance shading can appear with path-gradient colors and flat-path highlights.
Click a mapped path to inspect its original samples and distance information;
where no prepared hit geometry is found, inspection tries a nearby network snap.
Clearing the start removes distance shading and updates the planner's shared
start; the other layer switches keep their settings. Shell display requires a
prepared SQLite network and matching numeric routing artifact. Datasets without numeric routing retain gradient
display and inspection, with the distance control disabled.

## Plan run

Choose **Off / One way / Round trip / loop** in the Plan run heading. Off is the
initial selection. Either planning choice reveals endpoint controls, supported
hill preferences, and Generate run in the panel footer.

1. Choose a Start if the shared start is not already selected. Second-point
   selection starts automatically. Choose a distinct **Destination** for One way
   or **Turnaround** for Round trip / loop. Drag either marker to adjust it.
2. For round trips, choose Orientation and adjust Loop width with the slider or
   by dragging the midpoint circle edge or handle. The centre stays at the
   endpoint midpoint. One-way planning hides these controls.
3. Adjust Avoid hills where supported, then select Generate run. Generate again
   after moving endpoints or changing routing settings.
4. Your run shows the selected route's distance and direction arrows. Use Fit run
   to frame it. Details help shows the destination distance for one-way routes;
   round-trip details include outbound/return distances, repetition, and shape
   diagnostics. Available terrain estimates appear in details for either type.
   Cancel stops waiting in the browser; server work has its own deadline.

One way finds one legal directed path to the destination, minimizing distance
plus enabled route preferences. It can succeed where no legal return exists.
Round-trip shape settings have no effect on a one-way route.

Round trips return to the start. Nonzero Loop width produces up to two candidates
using opposite side preferences around the midpoint; Orientation chooses the
candidate displayed. Centre, side, and reuse preferences are soft: crossing the
centre, retracing, and equivalent candidates remain possible. Increasing width
does not guarantee a longer run or enclosure of the midpoint. Zero width still
plans a round trip, disabling shape penalties while keeping hill avoidance active;
both Orientation choices show the single candidate.
The Distance from start budget does not constrain either planner type.

Changing route type retains endpoints and loop settings, cancels pending browser
requests, and clears the previous result. Switching Off hides controls and editing
markers while keeping the last generated route visible. Returning to the same
type retains its result; changed routing inputs are marked out of date. Clear
resets the run and shared start. Map layers retain their settings throughout.
While no planner endpoint is being picked, map clicks remain available for path
inspection or distance-start selection.

Avoid hills offers **Off / Low / High** when prepared measurements are available.
It penalizes terrain-derived excess effort, with an additional preference for
gentler climbs. Flat ground and moderate descents add no hill penalty. Missing,
bridge, and tunnel measurements use a conservative fallback reported separately.
The weights and formula are in the [routing reference](reference/routing-mathematics.md).
Traffic-light, busy-road, and greenery preferences are not implemented.

The summary shows actual route distance; round-trip details include repeated distance.
Hill-enabled results include estimated ascent/descent and terrain coverage;
Off results leave terrain observations unavailable. A retraced route is labeled
“Out and back”. Completed candidates can survive the second candidate's
deadline expiry. Running time, fixed target distance, editable waypoints, retained
sections, and GPX export are not available.

## Shared settings and coverage

Open Settings from the map legend. **Paths & routing** offers all prepared
candidates or explicitly paved/gravel surfaces, with unknown surface tags kept
explicit. Stairs are avoided by default. Access, pedestrian direction, and
prepared route boundaries govern inspection eligibility,
snapping, exploration, and planning. Display filters cannot restore paths rejected
during import. The default snap radius is 50 m, configurable up to 200 m under
Endpoint snapping.

**Map appearance** contains Elevation contours and Hide building-access dead ends.
The latter hides conservatively identified building-access dead ends.
This affects colored geometry only: legal snapping and routing still use those
branches. The separate basemap may continue to show them faintly.

The terrain region and default capital-area path dataset use the same
40 × 40 km square near Leppävaara, with a visible path coverage boundary.
This is a terrain-aligned footprint rather than complete municipal coverage.
The smaller Leppävaara/Kilo dataset remains available as an optional backend
configuration. Panning does not download more analysis data. Outside the
prepared network, basemap paths are context.
Missing path artifacts leave the terrain map available; corrupt artifacts report
a failed path service. Missing connections mean no connection in prepared data,
not proof that none exists on the ground.

Terrain background opacity does not change prepared path measurements. Other
background smoothing values are available through the terrain API.
Source-elevation contours occur every 5 m, with 25 m contours emphasized;
they show terrain shape but have no elevation labels to resolve uphill sign.
Path tiles appear from zoom 10 through 16 and overzoom thereafter; coarse colors
summarize intervals, while inspection retains original measurements.

Distance exploration offers visible **Distance from start**, **Extra travel (distance)**, and **Extra travel (%)**
choices. Extra travel highlights detours around obstacles within the distance budget; see
[distance shells](distance-shells.md).

Grid distance contours use Light smoothing by default. Advanced options offers
Off, Light, or Strong contour smoothing; path inspection distances stay unchanged.

# Smooth loop routing experiment

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

Status: planned. First experimental slice supporting M7/M8; this does not
change the map-first delivery order.

Application delivery is described in the
[first UI-driven run planner](27-first-routing-ui.md).

## Random Delaunay prototype

Run the standalone random-sample experiment:

```sh
uv run --extra experiments python -m runmap.experiments.smooth_loops
```

The graph uses 8,000 uniform random samples in a 100 × 80 m rectangle, plus
exact home and turnaround anchors at (10, 40) and (90, 40). Delaunay triangle
edges define symmetric adjacency with Euclidean lengths. Use `--seed` (default
42) and `--samples` to vary the reproducible graph. Unlike the former regular
grid, connections have variable lengths and no fixed compass directions.

Outputs are `data/experiments/smooth-loops/evolution.png` and `results.json`.
`graph.json` saves points, adjacency, endpoints, seed, and sampling bounds.
The figure shows outbound expansion snapshots at 25%, 60%, and completion,
the completed return search, and both paths. Percentages refer to the final
expansion count, not elapsed time; colours indicate expansion order.
JSON records both side assignments, settings, paths, costs, lengths, overlap,
expansion counts, and elapsed time including edge-weight preparation.

Reusable functions live in `runmap.experiments.smooth_loops`. The experiment
keeps its traced A* separate from production routing while exploring costs.
Change `settings_list` in `main()` to compare other settings. The initial
random graph at seed 42 gives round-trip lengths 165.88, 212.42, and 241.18 m
at strengths 0, 2, and 8 respectively. The zero setting also disables side
and reuse penalties; the other two share side=2 and reuse=4. Nonzero settings
have zero shared edge length. Both side assignments take about 0.10–0.11 s
each including preparation on the development machine in the initial run.

Paths follow the triangulation edges and approximate rounded detours.
This prototype does not yet simulate obstacles, running speeds, deadlines,
or implement all milestone metrics. Smooth field integration uses at least
four midpoint samples per edge, increasing the count for edges longer than
four metres to keep sampling intervals at most one metre. Convex-hull edges
can be much longer than typical interior edges; this graph has no obstacles.

## First milestone — tunable outings around a centre

### Obstacle experiment

```sh
uv run --extra experiments python -m runmap.experiments.smooth_loop_obstacles
```

This compares open space, a rectangular obstruction on the preferred outbound
side, a vertical wall with a six-metre crossing gap, a four-metre access
corridor from home, and a fully disconnecting wall. All cases use the same
seeded Delaunay graph. Hard rectangles remove entire intersecting edges,
including boundary contact; removing only nodes would allow edges to cross
obstacles. Removed connections are not retriangulated across the obstruction.

Outputs in `data/experiments/smooth-loop-obstacles/` include one evolution
figure per reachable scenario and `results.json` with geometry, settings,
both side assignments, metrics, and explicit unreachable outcomes. Grey
rectangles show the hindrances. Strengths two and eight use identical side
and reuse settings to the open-space experiment.

Initial seed-42 results at strength two: open 212.42 m, blocked-left 229.52 m,
narrow crossing 185.86 m, and narrow access 211.46 m. The complete wall is
unreachable at both strengths. Narrow passages can force routes back toward
the centre and prevent the intended enclosure; completeness alone does not
establish a successful go-around. The corridor still allows different edges
in each direction, so it does not force segment reuse. A separate synthetic
shared-stem test verifies that unavoidable retracing remains permitted.

### Objective

Produce a closed outing from two A* searches with opposite side preferences.
Prioritize fast responses, understandable controls, and useful routes across
different starts. Finding the globally optimal route pair is not a goal.

The user selects home and a turnaround point. A smooth centre penalty pushes
the outing away from the area between them. Increasing its strength should
encourage wider outings without requiring a larger hard exclusion disk.
Routes still follow the available network, so changes can be discrete.

### Geometry and controls

Work in local metric coordinates. Initially place the centre halfway between
home and turnaround. Their connecting line defines fixed left and right sides
for both searches. Require distinct endpoints and expose centre scale in
metres, centre strength, side strength, and segment reuse strength.

The displayed disk represents the centre scale, not a forbidden boundary.
Keep its centre and scale fixed while varying strength so the effect is easy
to interpret. Centre movement and automatic turnaround selection are later
experiments.

### Tune using avoidance distance

Use avoidance distance `a` as the intended tuning parameter, with raw centre
strength retained as an internal value and diagnostic. Define `a` as the
distance from the centre where the radial penalty equals the base cost per
metre. For the initial distance-based objective, this means one metre there
costs two equivalent metres before side and reuse penalties:

```text
strength * exp(-a^2 / (2 * radius^2)) = 1
strength = exp(a^2 / (2 * radius^2))
a = radius * sqrt(2 * ln(strength))   # strength >= 1
```

Larger avoidance distance therefore pushes a fixed cost contour outward.
It does not guarantee minimum route clearance, enclosure, or total length.
The Gaussian scale `radius` remains a separate shape parameter: keep it fixed
when comparing avoidance distances. At `a = 0`, strength is one, not zero;
provide an explicit disabled setting for no centre penalty. Strengths between
zero and one have no contour at which the penalty equals the base cost.

Optionally expose `avoidance_ratio = a / distance(home, turnaround)` to make
settings comparable across outing scales. Reject coincident endpoints. Record
the ratio, derived metres, Gaussian radius, and derived strength with results.
A normalized Gaussian radius can later accompany this ratio when scaling the
whole geometry, but do not silently change radius while tuning avoidance.

For the current 80 m endpoint separation and 12 m radius, strengths two and
eight correspond to avoidance distances 14.13 m and 24.47 m, or ratios 0.177
and 0.306. Use approximately 0.18 as an initial experimental default for this
fixture, subject to review on other graph densities, seeds, and real networks.
It is not yet an adopted product default.

The larger-strength sweep on the same Delaunay fixture, holding side=2 and
reuse=4, produced lengths of approximately 212, 241, 258, 264, 274, and 303 m
for strengths 2, 8, 16, 32, 64, and 256. At strength 16 the routes already
touched the rectangle's top and bottom boundaries. Mark boundary-limited
results and enlarge the sampling domain before interpreting stronger settings
as useful route widening. Bound supported ratios/strengths and validate the
exponential conversion to prevent overflow or ineffective extreme settings.

When switching the base objective to time, define the contour relative to an
explicit reference pace `q` in seconds per metre:

```text
centre_strength_seconds_per_metre = q * exp(a^2 / (2 * radius^2))
```

The penalty at `a` then equals `q`, rather than the varying local hill-adjusted
pace. Record the reference pace; this preserves the control's interpretation
without making the spatial penalty depend on local slope.

### Smooth penalties

Start with a Gaussian radial field:

```text
centre_field(p) = exp(-distance(p, centre)^2 / (2 * radius^2))
```

This is strongest at the centre and decays smoothly beyond the displayed disk.
Integrate it along the actual path geometry, rather than checking nodes or
charging a fixed amount per edge. This makes the penalty approximately
independent of how OSM splits a path into segments.

Let `y(p)` be signed perpendicular distance from the home-to-turnaround line,
positive on its left. Use a smooth transition for the discouraged side:

```text
right_field(p) = (1 - tanh(y(p) / transition_width)) / 2
left_field(p)  = (1 + tanh(y(p) / transition_width)) / 2
```

Use a positive transition width in metres, initially an experiment default.
The outbound-left search uses `right_field`; its return uses `left_field`.
Never redefine these sides when reversing travel direction.

For each directed arc:

```text
cost = base_cost
     + centre_strength * integral(centre_field ds)
     + side_strength * integral(discouraged_side_field ds)
     + reuse_strength * reused_length
```

All contributions are finite and non-negative. With base cost in metres,
strengths are dimensionless; with base cost in seconds, strengths are seconds
per metre. Record the units with the experiment settings.

Use bounded-spacing quadrature over path geometry and check convergence on
long edges and paths crossing the centre or dividing line. Cache field
integrals per physical segment for fixed geometry and settings; changing
strength only reweights those integrals. Geometry penalties are symmetric,
while base travel time can differ by direction.

### Construct candidates

1. Search home to turnaround with the right-side penalty.
2. Search turnaround to home with the left-side penalty and a soft penalty
   for reusing any outbound physical segment, in either direction.
3. Repeat with outbound-right and return-left, producing at most two candidates.
4. Validate each combined directed walk, measure it, and display both valid
   candidates. Do not hide tradeoffs behind a single combined score.

Shared access stems are permitted. Access restrictions remain hard filters;
centre, side, and reuse preferences remain soft. If a leg is unreachable,
report that outcome. If a deadline expires, distinguish incomplete search
from unreachable endpoints and retain any already completed valid candidate.
Do not introduce unbounded retries to force a loop.

### Separate geometry from running-time calibration

First run with distance as base cost to isolate the route-shaping behavior.
Keep the cost calculation replaceable, then compare with directional running
time summed over terrain-profile intervals using a configurable grade-to-speed
model. Never derive time solely from an arc's average endpoint grade.

For distance, use straight-line distance as the heuristic. For time, divide
straight-line distance by the maximum permitted model speed, including
downhill speeds. Non-negative preference penalties need not appear in the
heuristic. Keep actual distance/time separate from artificial penalties.

### Inspect and measure

Provide a local repeatable experiment with a map preview and saved settings,
route geometry, and metrics. Show home, turnaround, centre, disk scale,
dividing line, and differently coloured outbound and return legs. Overlay
the smooth field so its influence can be inspected.

Record per candidate:

- Actual distance and, when enabled, predicted time and ascent.
- Centre, side, and reuse cost contributions separately.
- Repeated physical distance, counting traversals after the first.
- Closest approach to the centre and distance inside the displayed disk.
- Whether the closed walk winds around the centre, with boundary crossings
  and self-intersections identified rather than assumed to form a simple loop.
- Preparation and per-search latency, expanded nodes, and total elapsed time.

Enclosing the centre is evidence about route shape, not proof that the entire
disk is avoided. A valid outing that fails the intended shape is reported as
such, rather than presented as a successful go-around.

### Acceptance evidence

Use synthetic graphs for two clear sides, shared stems, a single bridge,
unreachable turnaround, long edges crossing the penalty field, and asymmetric
travel times. Confirm that zero preferences recover the base-cost search and
that splitting geometry does not materially change integrated penalties.

Review several Leppävaara examples, including sparse paths and constrained
crossings. For fixed endpoints and scale, sweep centre strength from zero
through several increasing values; separately sweep side and reuse strengths.
Save comparisons showing where routes widen, switch corridors, or fail to
surround the centre. Wider distance or enclosure is not guaranteed to change
monotonically, even though the penalty field itself changes smoothly.

Measure cold preparation and warm searches separately on the named development
machine. Use one second for both candidate constructions on a warm graph as
an initial experimental target, not an established performance promise.
Bound the complete experiment with a configurable deadline and record misses.

Complete when the experiment is reproducible, tuning produces understandable
route changes, access and connectivity remain valid, and measured results
identify useful defaults and failure cases. Do not require an exact target
length, a globally optimal pair, or successful enclosure at every start.

Later work can add target-duration tuning, richer speed calibration, movable
centres, or explicit enclosure constraints if the first results justify them.

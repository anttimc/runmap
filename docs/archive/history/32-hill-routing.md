# Hill routing with a preference for gentle climbs

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

For a self-contained derivation of every route cost term, see
[Run planner: cost calculation](../../reference/routing-mathematics.md).

Status: Minetti-based uphill preference implemented 2026-10-19; preset calibration and local runner
acceptance remain provisional. Delivers step 3 of the
[cost architecture](31-routing-cost-architecture.md).

## Purpose and source

The planner minimizes the combined route score. Distance provides the baseline
cost for graph search; preferences can make a longer loop preferable. The current
planner builds two legs greedily and does not guarantee a globally optimal loop.

The running energy function comes from Alberto E. Minetti, Christian Moia,
Giulio S. Roi, Davide Susta, and Guido Ferretti (2002), *Energy cost of walking and
running at extreme uphill and downhill slopes*, Journal of Applied Physiology
93:1039–1046, [doi:10.1152/japplphysiol.01177.2001](https://doi.org/10.1152/japplphysiol.01177.2001).
[Readable paper PDF](https://runscribe.com/wp-content/uploads/power/Minetti2002.pdf).
We use the **Cr fifth-order regression in the Figure 1 caption**, not the walking
fit Cw or a new regression on the table values. Cr is energy per mass per metre
travelled, in J/kg/m. The investigated slope range was −45% to +45%.

## Formula and route score

For signed grade `g` in percent, `i = g / 100` is rise/run, not degrees:

```text
Cr(i) = 155.4 i^5 − 30.4 i^4 − 43.3 i^3 + 46.3 i^2 + 19.5 i + 3.6
energy_ratio(i) = Cr(i) / Cr(0)
uphill_ratio(i) = 1 + (energy_ratio(i) − 1) * (g / 5)^2       when g > 0
uphill_ratio(i) = energy_ratio(i)                           otherwise
response(i) = uphill_ratio(i) * sqrt(1 + i^2)
hill cost = weight * integral(max(0, response(i) − 1) ds_projected)
route score = graph distance + shape costs + hill cost + other preference costs
```

`Cr(0) = 3.6` is the fitted flat intercept; normalization deliberately uses this
value rather than the measured flat mean. This expresses energy in equivalent
flat-running metres without requiring body mass. `sqrt(1+i^2)` converts each
projected sample span into estimated sloping travel distance. This geometric
conversion, uphill adjustment, and preset weights are application choices, not
fitted study results. The underlying `running_energy_cost` still evaluates the
published Cr fit; `grade_response` applies the product preference on top.

The uphill adjustment responds to runner feedback that mild sustained climbs are
preferable to short steep climbs. Below 5% it reduces excess above flat; at 5% it
preserves the original curve; at 10% it multiplies excess by 4; at 20% by 16.
This shifts the balance from total climbing energy toward avoiding steep climbs.
For the same 10 m gain, 200 m at 5% now beats 100 m at 10% at both presets, when
other costs agree. This is an illustrative tradeoff, not a promise that every
longer gentle alternative will win. There is no upper cap.

The hill term charges **only excess above flat running**, clipped at zero.
Flat spans and moderate descents contribute no hill penalty. Very steep
descents can still contribute when their energy exceeds flat running.
Subtracting the baseline prevents the hill preset from adding another distance
charge that overwhelms the loop-shape preferences. Clipping prevents downhill
rewards and negative edge costs. There is no upper saturation cap.
The distance heuristic remains admissible because total arc cost is at least
graph distance. Physical distance and ascent/descent diagnostics keep their
original meanings; only the optimization score changes.

The model is `minetti-excess-uphill-preference-v4`; the weight table is
`minetti-excess-equivalent-metres-v4`. Weights remain unchanged from v3:

| Preset | Weight | Score per flat metre, excluding shape costs |
| --- | ---: | ---: |
| Off | 0 | 1 |
| Low | 1 | 1 |
| High | 6 | 1 |

Low and High scale slope excess without changing the distance or shape weights.
The objective represents hill avoidance rather than total metabolic energy.
The presets can select different routes. They do not
promise a particular detour length or minimum ascent. The weights are initial
product calibration, not physiological constants.

## Beyond the fitted range and uncertain terrain

Within ±45%, evaluate the published polynomial exactly. Beyond either endpoint,
continue along that endpoint's tangent:

```text
b = nearest fitted boundary (−0.45 or +0.45)
Cr_extended(i) = Cr(b) + Cr'(b) * (i − b)
```

This is an explicit application extrapolation, not a result from the paper. It
preserves value and first derivative, stays positive, and continues rising as
slopes become more extreme. Unrestricted fifth-order extrapolation would
eventually produce negative downhill energy. The policy avoids that failure
without flattening the curve or treating a valid steep slope as missing.
Nonfinite inputs and nonfinite/nonpositive computed energy are rejected.

Missing terrain, bridges, and tunnels use a direction-independent fallback
response equal to the **excess of an unadjusted Cr +20% climb above flat**,
including the travel-distance conversion.
This is a conservative application assumption, not an estimated actual slope
and not a maximum: trusted steeper terrain can cost more. It adds approximately
155 equivalent m at Low and 931 equivalent m at High per 100 projected m.
The new uphill multiplier is deliberately not applied to this unknown-slope
fallback: unknown terrain is not evidence of a steep climb.
An entirely absent profile and an explicitly invalid interval use the same rule.
All connections remain subject to the shared access and stairs policies.

Numerical coverage and trusted coverage are reported separately. Valid bridge
and tunnel ground samples contribute to ascent/descent but remain untrusted for
energy. Missing samples invent no height change; entirely unmeasured traversals
report null ascent/descent. Partial coverage gives incomplete height estimates.
Terrain-derived energy is a route preference, not a personalized calorie or
speed prediction; it does not model footing, fatigue, or downhill safety.

## Preparation and UI

`build_hill_data` validates graph/measurement identity and prepares forward and
reverse unweighted responses once at dataset load. No terrain resampling, OSM
download, graph rebuild, or new on-disk format is required. Negating signed grade
recomputes Cr for reverse traversal; it does not negate energy.

Endpoint cuts use graph-chainage coordinates to scale the corresponding physical
sample contributions. A climb and descent on one segment contribute separately;
whole-segment mean grade is never substituted. Directed partial integrations
are cached once per request across both candidate assignments. Changing presets
only changes the weights. Gaps inside a nonempty profile are rejected.

Zero loop width disables shape costs. Active energy preferences still search
outbound and return paths independently, returning one candidate. With all
preferences Off, the existing legal retracing optimization is retained and hill
integration is skipped. Off responses retain null terrain observations.

The Plan run panel offers an Off / Low / High slider, with visible labels and a
description of the selected energy preference. Its help links to the paper and
explains downhill behavior. Terrain notes show fallback usage. Changes cancel
obsolete requests and mark displayed results stale; mode changes preserve the
chosen level. Map background smoothing does not change these features.

## Direction audit

Both nonzero-width candidates are optimized separately: home to turnaround with
one side preference, then turnaround to home with the opposite side preference
and that candidate's reuse context. The second candidate repeats both searches
with the side assignments exchanged. These are four directed searches, not a
single loop reversed for display. Hill features cache by directed arc identifier,
so reversing a traversal uses negated grade and recomputes both Cr and the uphill
adjustment. Physical partial-span features preserve that direction.

Clockwise/counter-clockwise are soft spatial preferences, not enforced winding
constraints. Both searches can select identical directed legs, or the same
physical loop in reverse. The former can happen when slope costs dominate the
side penalty or no useful alternative exists. The API marks equal physical
geometry with `equivalent_to`; it does not force a different second route.
Switching the candidate selects an already computed result rather than submitting
a new request. Zero width intentionally returns only one candidate.

Regression checks compare each of the four searches with a separate Dijkstra
search using its own direction, side preference, and reuse context. They also
check a longer 5% ascent against a short 10% ascent with the same height gain,
including the separately optimized downhill return.

## Calibration artifacts and reproduction

[Penalty plot](32-hill-penalties.png) ([SVG](32-hill-penalties.svg)) shows the
current weighted preference contribution per 100 projected metres, alongside
the unadjusted High curve for comparison. Flat terrain has
a zero contribution at every preset.

Regenerate it using:

```sh
uv run --offline --no-sync python -m runmap.experiments.plot_hill_penalties
```

The original [measurement report](32-hill-routing-results.json) and
[browser report](32-hill-routing-browser.json) describe the superseded capped
`terrain-hills-v1` model and four-choice dropdown. They are historical evidence,
not validation of current routes or controls. Preserve them for comparison.

Record current model observations separately:

```sh
uv run --offline --no-sync python -m runmap.experiments.benchmark_hill_preferences \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  docs/32-hill-routing-excess-results.json \
  --case agnes-west-to-salmintie --case agnes-central-to-salmintie \
  --case agnes-east-to-salmintie --width 0.18
```

The command above records three street endpoint pairs at width 0.18 and each
preset (9 requests). With no case/width flags, the benchmark records the original
four endpoint pairs at widths 0 and 0.18 (24 requests), with exact snaps, model/weight identity, leg costs,
traversals, terrain coverage, timing, and expanded states. Off terrain is measured
after generation for comparison. Review real routes before settling the weights,
especially uncertain connectors, stairs, steep descents, and High detours.

The historical [v2 weight report](32-hill-routing-minetti-results.json), using Low 0.5 and
High 3 before their doubling, records all 24 requests
completing, with generation taking 0.086–1.070 s and load/preparation 6.908 s.
For the initial zero-width outing, Off and Low use 3,818.9 m; High uses 3,833.4 m.
Its unweighted flat-equivalent energy falls from 3,974.9 to 3,948.1 m. These are
single-run observations, not performance guarantees or runner acceptance.

The historical [gentle-climb report](32-hill-routing-gentle-results.json) uses the v3
uphill adjustment and Low 1 / High 6 weights. All 24 requests completed in
0.085–1.147 s. The initial High outing uses 4,221.6 m at zero width; at width
0.18 the candidates use 4,353.3 and 4,473.3 m with different directed legs.
At High, the school and rolling-path examples return identical directed routes
for both side assignments; the initial outing and bridge example differ.
These observations confirm that independent direction searches can either agree
or choose different paths, and do not imply local runner acceptance.

## Verification

All 178 Python tests pass, and frontend typecheck/build passes for v4.

Checks cover published coefficient values and percent conversion, normalization
and sloping length, cheaper moderate descents, increasing steep-slope costs,
continuous positive tangent tails, directed reversal, partial-span conservation,
fallback for absent and invalid profiles, request caching, A* agreement with
Dijkstra, and selection of a longer route under High. Existing API tests cover
three-level capabilities, rejected Medium requests, and cost diagnostics.

The revised slider and hill help still need a browser check. Local runner
acceptance remains pending.

## Agnes Lundellin arkadi–Salmintie investigation

The v3 full-energy baseline reproduced shorter loops on enabling avoidance.
With home `(24.8031897, 60.217934)` and turnaround
`(24.8034464, 60.2095759)`, default width 0.18 and the first candidate:

| Model / preset | Loop distance | Uphill distance above 5% | Steepest uphill sample |
| --- | ---: | ---: | ---: |
| Off | 3,243 m | 223 m | 19.2% |
| v3 High | 2,405 m | 286 m | 12.1% |
| v4 Low | 3,420 m | 176 m | 7.7% |
| v4 High | 3,406 m | 69 m | 5.5% |

All these routes have trusted terrain throughout; fallback costs did not cause
this behavior. The v3 hill term counted approximately one full extra distance
charge per unit weight even on flat terrain, reducing the relative influence
of the existing shape and reuse costs. Its downhill discounts also offset
uphill costs. The v4 positive excess term removes both effects while keeping
the published Cr function and the application uphill adjustment intact.

[Local comparison data](32-hill-routing-excess-results.json) records all three
tested home positions, both candidates and all presets under v4, with exact
requests, snaps, traversals, terrain measurements and cost breakdowns.
The pins above are representative street locations; the user's exact pins
were not available during this investigation. Slopes are sampled terrain
estimates, not surveyed road grades. Some corrected loops remain shorter than
Off while improving slopes; route length alone is not an avoidance metric.
The planner still optimizes legs greedily and has no fixed target distance.

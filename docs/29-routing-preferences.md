# Run routing preferences

Status: existing-data audit, preference cost contracts, and hill avoidance implemented,
2026-10-15; other preference algorithms remain planned. Extends the distance-based
[first run planner](27-first-routing-ui.md), using the controls described in the
[UI and settings plan](28-planner-ui-and-settings.md).

The [local data audit](30-routing-preferences-audit.md) records reproducible
counts and 13 source-derived fixtures. Existing terrain supports the first
hill-cost increment. Signal tag retention and passage attribution need work;
busy-road and green preferences require complete buffered context acquisition.
Context fixtures and local runner validation remain open parts of the audit.

The [cost architecture plan](31-routing-cost-architecture.md) specifies the next
increments. Shape-cost extraction, explicit request data/cache, typed preference
settings, capability validation, traversal references, and per-leg diagnostics
are implemented. [Hill avoidance](32-hill-routing.md) now supports all four levels
on datasets with prepared terrain. Traffic lights and environmental preferences
still support only Off; their data/attribution gates remain open.

## Outcome

Let a runner trade modest detours for fewer hills, signal-controlled crossings,
less exposure to busy roads, and more green surroundings. Preserve fast,
reproducible, valid routes and report understandable tradeoffs. Seek useful
routes, not a globally optimal pair or an exhaustive Pareto frontier.

The current planner uses distance plus centre, side, reuse, and optional hill
penalties. It does not yet optimize traffic lights, road exposure, greenery, or
running time. Hill avoidance remains independent of the loop-shape control.

## Cost model and constraints

First keep distance as the base cost and express added costs in equivalent
metres. This gives tuning a concrete interpretation: a crossing penalty of X
metres means the planner may accept roughly X extra metres to avoid it, subject
to all other costs. Values are preference weights, not measured delays.

For a directed traversal, use:

`cost = distance + shape + hill + signal + busy-road + non-green penalties`

All contributions must be finite and nonnegative. Apply weights to measured
features; store features separately from the chosen weights. Keep the existing
distance heuristic admissible because total cost remains at least distance.
Check that invariant for every supported cost configuration.

Physical/access rules remain mandatory. Surfaces and stairs retain their
existing eligibility controls. New preferences begin as soft costs; do not
silently convert High into a hard exclusion. Strict exclusions and automatic
relaxation are outside this increment.

Later, a calibrated slope-to-speed model can replace distance with running
seconds and express preference costs in equivalent seconds. That is a distinct
cost-model version, requiring weight recalibration and a time lower bound using
the maximum supported running speed, including downhill. Never add metre costs
directly to seconds or present preference cost as predicted running time.

## Data audit before implementation

Inspect the actual source extract and prepared network before choosing tag
rules or promising coverage. Record retained/dropped attributes, known examples,
coverage, source identity, preparation version, and missing-data behaviour.
Consult source documentation when implementing the extraction rules.

The current graph importer retains selected way tags and node attributes such
as highway and crossing. This does not establish that all signal information
survives extraction or graph construction. Green polygons and the surrounding
major-road network need their own audit; runnable segments alone are not enough.
Existing signed terrain intervals are a starting point for hills, with their
coverage and bridge/tunnel uncertainty preserved.

Prepare context in a buffer outside the runnable graph boundary so exposure
does not disappear artificially at extract edges. Record where contextual
coverage is insufficient. Unknown observations must not be reported as zero
signals, flat ground, quiet roads, or confirmed greenery.

## Preference features

### Hills

Integrate nonnegative excess above flat from the running energy fit in Minetti et al. (2002), Figure 1,
along measured intervals, with an application adjustment that softens uphill
excess below 5% and increasingly penalizes steeper climbs. Use signed grade in traversal direction; reverse
traversal must reverse the grade. Flat ground and moderate descents add no hill penalty;
steep climbs and descents remain distinct without a penalty cap. The formula,
source, preset scaling, and policy outside the fitted range are documented in
[hill routing](32-hill-routing.md). Avoid classifying missing terrain as flat or
deriving a path cost from only its mean grade.

Report estimated ascent/descent and valid-terrain coverage separately from the
penalty. Treat bridge/tunnel ground estimates as uncertain. Decide an explicit
fallback for uncertain intervals during the audit and show its use in results.
“Avoid hills” is a preference, not a promise of minimum effort or fastest time.

### Traffic lights

Identify signal-controlled pedestrian crossings and attach a stable event
identity to the traversals that actually cross them. Charge once per crossing
passage, including a second charge if the run passes the crossing again.

Do not charge every adjacent arc or every nearby signal point. Audit multi-node
crossings, islands, separate carriageways, signals controlling parallel vehicle
movements, and starts/turnarounds at a crossing. Define event boundaries and
endpoint charging explicitly. Preserve event identity through endpoint splits.

Prefer an additive directed-arc assignment where reliable. If charging depends
on an incoming/outgoing pair, extend search state to include the required arrival
context; a node-only search cannot correctly model a turn-dependent cost.
Choose between these designs from real fixtures before implementation.

Initially use equivalent-metre penalties, with no claim about actual wait time.
Report mapped signal crossings and unresolved evidence separately.

### Busy roads

Build a spatial index of relevant road geometry, including roads excluded from
the running graph. Start with documented road-class weights as an exposure
proxy. Optional traffic-volume data can refine this later; do not invent counts.

Integrate a smooth distance-decaying field along the entire running path.
Choose and document the influence range and decay function. Combine nearby
roads with a bounded rule, initially the maximum weighted influence, so duplicate
carriageway geometry does not double the cost automatically.

Review paths parallel to major roads, short perpendicular crossings, underpasses,
bridges, and separated paths. Use available layer/bridge/tunnel evidence to
avoid plainly incorrect associations. This is a road-proximity proxy, not a
noise, pollution, or safety estimate. Report exposure distance or integrated
exposure with the model definition.

### Green surroundings

Prepare candidate green-area geometry with documented inclusion rules and
source coverage. Determine eligible categories from the local audit rather than
assuming all open land is green or all green land is accessible.

Compute a bounded green-context score along each path, optionally with a smooth
boundary transition. Penalize non-green exposure instead of subtracting a green
reward, keeping costs nonnegative. Green surroundings never create a traversable
connection or override pedestrian access.

Report green-context distance/share and coverage. Distinguish a mapped non-green
area from unavailable contextual data. Review parks, wooded paths, park edges,
private green land, and green corridors beside major roads. Independent greenery
and traffic preferences may conflict; show both observations in the result.

## Integration and performance

Use data-only feature/settings/result containers and module-level preparation,
integration, validation, and cost functions. Keep the search engine independent
of data-source rules. Compose cost terms through its existing arc-cost hook,
except where signal event modelling demonstrably requires expanded state.

Precompute static grade/exposure features during dataset preparation. Retain
chainage-aware intervals or cumulative integrals so endpoint splits receive
only their actual share. A single whole-segment average is insufficient for
partial routes. Reversing a traversal changes directional hill/event features
but preserves symmetric spatial exposure. Splitting geometry must not create
extra crossing events or change integrated spatial cost materially.

Cache features by dataset and preparation/model version. Slider changes should
reweight existing features rather than repeat spatial joins. Continue sharing
centre-field work within a request, enforcing bounded concurrency, cancellation
protection, and a deadline across preparation and both candidates.

Apply the same user preferences to both legs and both side assignments. Keep
reuse and shape penalties separate in diagnostics. With loop width zero, disable
shape costs but retain other preferences; retrace the preferred outbound route
only when return costs are symmetric and directions permit. Otherwise search
the preferred return explicitly. This matters for directional hill costs.

## UI and diagnostics contract

Expose Off / Low / High per preference. Define separate versioned
weight tables, starting with Off as the backward-compatible default. Tune Low
to make modest changes and High to accept larger detours; define examples and
detour observations rather than promising fixed detour bounds.

Advertise supported features, defaults, model versions, and coverage through
configuration. Return the resolved settings with results, plus actual distance,
per-term costs, raw feature observations, coverage, and timing. Reject invalid
weights and unsupported requested preferences explicitly.

The UI marks runs stale when preferences change and explains unavailable data.
Show raw observations when comparing runs; the artificial score is diagnostic.
One selected setting still produces at most the existing two side assignments.
Automatic multiobjective candidate generation is later work.

## Delivery slices

1. Audit data and create a small local fixture set for each preference. Resolve
   signal event semantics, uncertainty defaults, and context coverage. Record
   which dataset preparation changes each preference requires.
2. Add reusable feature/cost composition, settings validation, versioning, and
   per-term diagnostics. All preferences Off must preserve baseline behaviour.
3. Deliver hill avoidance using prepared terrain intervals; review directional
   costs and update zero-width return handling. Do not require a speed model.
4. Deliver traffic-light avoidance after resolving event attribution; use
   expanded search state if the audited representation requires it.
5. Deliver busy-road exposure, then green-context preference, with buffered
   preparation and explicit coverage. Each can ship independently.
6. Calibrate combined preferences against familiar runs and measure performance.
   Add only the corresponding supported controls to the planned UI.

Running-time estimation, hard environmental exclusions, automatic turnaround
selection, and broader alternative generation remain separate follow-ups.

## Verification and acceptance

Test meaningful fixtures: an avoidable signal and an unavoidable crossing;
multi-part crossing charged correctly; signal endpoint splits; directional hill
routes; missing terrain; road crossing versus parallel exposure; grade-separated
roads; green/private land; partial segments; context boundaries; and preferences
in conflict. Verify integrated features survive splits and all costs remain
nonnegative. Check heuristic correctness against zero-heuristic search on small
fixtures, using the appropriate state representation.

Compare each preference alone and selected combinations against the all-Off
baseline. Record distance change, raw observations, expanded states, preparation
and search latency, and deadline outcomes. Larger weights need not produce a
monotonic metric for the final greedy two-leg construction; review route changes
instead of claiming global guarantees.

Accept a preference when local examples show understandable improvements,
unavoidable exposure still yields a valid route, uncertainty is visible, and
response time remains suitable for interactive regeneration. Keep accepted and
rejected examples with exact endpoints, dataset/model identity, and settings.

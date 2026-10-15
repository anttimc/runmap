# Modular routing cost architecture

Status: steps 1–2 implemented, 2026-10-15; preference algorithms
remain planned. Refines the cost-composition
slice of [routing preferences](29-routing-preferences.md), using the
[data audit](30-routing-preferences-audit.md). This document specifies the
refactor and its follow-up increments.

## Step 1 implementation

The planner now binds the module-level `costs.arc_cost` with `functools.partial`.
`RequestCostData`, `RequestCostCache`, `LegCostContext`, `ShapeFeatures`, and
`ShapeStrengths` make request/leg state explicit. Settings validation, strength
resolution, and lazy integration live in `cost_preparation`; component formulas
and scalar composition live in `costs`. Shared run records, geometry helpers,
and the deadline guard have independent modules. Existing planner imports of
settings/records, geometry helpers, validation, and tuple penalty strengths
remain available for API callers and tests.

Zero width still uses distance-only costs and legal retracing. Integration is
lazy and cached once per overlay segment across both legs and both assignments;
each planning call owns a fresh cache. Fixed leg contexts preserve side and
reuse isolation. Preference settings, new feature loading, breakdowns, and
changed zero-width semantics belong to later slices.

Verification: all 150 Python tests pass using
`uv run --offline --no-sync python -m unittest discover -s tests`. New regression
checks cover sharing integrations within a request, isolation across requests
and leg contexts, disjoint partial-segment reuse, expired cached costs, and
skipping integration at zero width. Another 120 fixed-seed comparisons against
the saved original implementation cover a small graph with alternative routes,
20 endpoint pairs, and six shape settings: candidate arcs, leg costs, expanded
states, distances, repeated distances, and overlay geometry match exactly.

## Step 2 implementation

`cost_settings` now resolves typed Off / Low / Medium / High settings through
separately versioned preference weight tables and explicit capability records.
All tables currently define Off = 0; other weights remain null until calibrated.
All four capabilities currently support only Off, with a reason and null feature
version/coverage. Non-Off requests fail before snapping or searching, including
with zero loop width. Malformed levels and unknown preference fields are rejected
by the API schema. The domain resolver also validates levels and weight tables.

`costs` composes explicit shape and preference contributions, validates finite
nonnegative components and `total >= physical length`, and constructs selected
leg breakdowns using the same component functions. Return explanations use the
actual outbound-used set fixed for that search. Breakdown objects are allocated
only for selected paths. Diagnostics read already cached shape features rather
than repeating integration.

Selected traversals now carry original segment IDs, ascending graph-chainage
spans, and a separate reverse flag. The API retains its existing directional
start/end traversal representation when serializing these references. Ordinary
unsplit arcs resolve lazily without copying the graph. Direction uses the shared
`arc_forward` helper, including its canonical arc suffix handling for self-loops.

API additions are backward compatible for existing requests:

- `POST /api/paths/run` accepts a top-level `preferences` object with `hills`,
  `signals`, `busy_roads`, and `greenery`; each defaults to `"off"`. Unsupported
  non-Off requests return HTTP 400, while malformed levels/unknown fields fail
  request schema validation.
- `/api/paths/config` adds preference defaults, capability records, weight tables,
  model version, and units under `run_planner`.
- Run responses include `cost_model` with version `distance-preferences-v1`,
  `equivalent_metres` units, resolved shape settings/strengths, preference settings,
  weights, table versions, and capabilities. Metadata is present for missing-snap,
  unreachable, and deadline results too.
- Each selected leg adds `cost_breakdown`: physical distance plus centre, side,
  reuse, hills, signals, busy-road, non-green contributions and total. Raw
  `observations` are a separate typed record. Unavailable ascent/descent, signal
  passages, and environmental observations/coverage serialize as null, while
  disabled preference costs are zero.

No new feature artifacts are loaded for Off requests and no preference controls
are enabled. Dataset versioning remains unchanged. The new feature observations
and non-Off algorithms are reserved for subsequent delivery slices.

Verification: all 162 Python tests pass. The suite covers preference/weight validation, unsupported
capabilities before work, additive API serialization, preserved reuse contexts,
physical split/reverse references, finite/lower-bound costs, and A* versus
Dijkstra on an alternative-route fixture. The frontend typecheck/build passes.
The 120 original fixed-seed comparisons still match old routing outputs exactly;
new leg totals agree with their search costs.

One local Leppävaara smoke comparison, home `(24.795, 60.216)` and turnaround
`(24.815, 60.225)`, completed both assignments with identical paths, search
expansion counts, and 5,273.998 m distance. Planning took 0.359 s with the saved
original implementation and 0.378 s with the new contracts/diagnostics, excluding
dataset loading. This single observation is not a latency guarantee.

## Current closure and intended separation

Before extraction, `plan_run` defined `cost(arc, right, used)` inside the request. It
captures the endpoint-overlay graph, projected endpoints, separation, derived
shape strengths, zero-width flag, deadline, and mutable field-integral cache.
The closure let both legs and both side assignments share lazy integrations
without passing those values separately on every call. The two small lambdas
bind the leg's side and outbound-used segments to the search's `arc_cost(arc)`
interface. This is useful request scoping, but the function also owns numerical
preparation and the complete cost formula.

Keep those lifetimes explicit in data records. Move preparation, validation,
and evaluation to module-level functions. Keep `plan_run` responsible for
endpoint overlay creation, candidate/leg sequencing, search invocation, and
result assembly. Preserve the search's callback interface; use
`functools.partial` to bind explicit cost data and leg context. There is no need
for a callable class, cost-provider inheritance, or a preference plugin registry.

## Modules and dependency direction

| Module | Responsibility |
| --- | --- |
| `routing/run_types.py` | Existing run settings, physical parts, overlay, candidate, and result records moved out of planner orchestration |
| `routing/cost_types.py` | Data-only preference settings, resolved weights, traversal references, cost context/cache records, feature summaries, and breakdowns |
| `routing/cost_preparation.py` | Resolve settings, map overlay arcs to physical spans/directions, obtain static features, and lazily integrate/cache request shape fields |
| `routing/costs.py` | Pure component formulas, scalar arc-cost composition, breakdown construction, and cost invariant validation |
| `routing/fields.py` | Existing numerical shape-field integration; keep it independent of routing orchestration |
| `routing/run_planner.py` | Assemble request data, select leg contexts, invoke searches, and assemble candidates |
| `routing/search.py` | Existing generic graph search with an arc-cost callback and lower-bound heuristic |

Shared records may refer to the existing graph/search types. Preparation may
import terrain feature helpers and `fields`; evaluation must not import source
extractors, API modules, or `run_planner`. Move projection/geometry helpers to
a small geometry module when extraction needs them, avoiding an import back
into planner orchestration. Preserve existing imports with re-exports during
the first extraction where API/tests rely on them.

Introduce preference-specific extraction modules only as their implementations
arrive. Existing baseline `CostModel` remains a separate experiment during this
increment; migrating it is not required to modularize the application planner.

## Data and lifetimes

Use frozen dataclasses for settings, weights, and immutable feature records.
The request cache is explicitly mutable and owned by one planning call; freezing
an outer dataclass does not make its dictionaries immutable. All functions and
fields have precise types, including `Mapping`, typed dictionaries, and literal
preference levels. Do not use `Any` or broad arbitrary cost-term dictionaries.

| Record | Contents | Lifetime |
| --- | --- | --- |
| `PreferenceSettings` | Off / Low / Medium / High for supported preferences | User request |
| `CostWeights` | Resolved nonnegative per-preference weights and model version; explicit shape strengths | User request |
| `TraversalRef` | Original physical segment ID, graph start/end chainage, and traversal direction | Endpoint overlay |
| `PreparedPreferenceFeatures` | Versioned, chainage-aware terrain/context features and coverage, independent of selected weights | Prepared dataset |
| `RequestCostData` | Overlay graph, traversal map, prepared feature references, shape geometry/settings, resolved weights | One planning call |
| `RequestCostCache` | Lazy shape integrals by overlay segment; static weighted preference totals by directed overlay arc | One planning call |
| `LegCostContext` | Which geometric side is penalized and a frozen set of outbound-used overlay segment IDs | One search |
| `CostBreakdown` | Distance, centre, side, reuse, hills, signals, busy-road, non-green, and total costs | Selected-route diagnostics |

Zero weights skip the corresponding feature work. With all preferences Off,
old prepared graphs remain usable and do not require new terrain/context files.
An enabled unsupported preference fails validation before searching; missing
feature support must not silently become zero cost. Distinguish unsupported
datasets from uncertainty inside a supported feature, which uses its documented
fallback and coverage reporting.

Cache keys include direction wherever features are asymmetric. Reuse symmetric
shape fields between directions and between candidate assignments. Keep all
costs deterministic for the lifetime of a search; populating a cache may change
performance, never the answer. Across-request caching is a later measured
optimization and would require dataset/model identity and shape geometry in keys.

## Construction and evaluation flow

1. Validate shape settings and resolve preference levels into versioned weights.
2. Construct the endpoint overlay as today, preserving directed eligibility.
3. Resolve each traversed overlay arc to its original physical span/direction.
   Prefer lazy resolution for ordinary unsplit arcs; do not eagerly copy the
   full graph or compute all preference features per request.
4. Construct request cost data and an empty request cache shared by candidate
   searches. Bind the outbound `LegCostContext` and run A*.
5. Freeze the outbound-used overlay segment IDs, bind the return context, and
   run the return search or apply the permitted retracing optimization.
6. Re-evaluate selected traversals with the same cost functions and their
   original leg contexts to produce diagnostics; sum physical distance separately.

Suggested function contracts, showing the division of responsibilities:

```python
resolve_cost_weights(shape: RunSettings, preferences: PreferenceSettings) -> CostWeights
build_request_cost_data(...) -> RequestCostData
resolve_traversal(data: RequestCostData, arc: Arc) -> TraversalRef
ensure_arc_features(data: RequestCostData, cache: RequestCostCache,
                    arc: Arc, deadline: float | None) -> ArcCostFeatures
evaluate_arc_cost(length_m: float, features: ArcCostFeatures,
                  weights: CostWeights, leg: LegCostContext,
                  reused: bool) -> float
evaluate_arc_breakdown(length_m: float, features: ArcCostFeatures,
                       weights: CostWeights, leg: LegCostContext,
                       reused: bool) -> CostBreakdown
arc_cost(arc: Arc, *, data: RequestCostData, cache: RequestCostCache,
         leg: LegCostContext, deadline: float | None) -> float
```

The construction function's full parameters will be specified from the extracted
records; the ellipsis here is a planning shorthand. `ArcCostFeatures` contains
explicit named fields for shape integrals and prepared preference contributions.
Per-arc fixed preference contributions can be weighted/cached once per request;
side and reuse contributions remain leg-dependent. Preparation owns cache
mutation. The outer `arc_cost` adapter checks the deadline, obtains features,
and calls the pure evaluator.

Planner search wiring becomes conceptually:

```python
cost_callback = partial(arc_cost, data=cost_data, cache=cost_cache,
                        leg=outbound_context, deadline=deadline)
outbound = shortest_path(graph, home, turnaround,
                         arc_cost=cost_callback, heuristic=heuristic,
                         deadline=deadline)
```

This binding still supplies request context to a callback, but no substantial
cost logic lives inside the planner. Component formulas are ordinary functions
that can be exercised without invoking route generation or loading OSM.

## Cost composition and diagnostics

Use equivalent metres throughout:

```text
total = physical length
      + centre + side + reuse
      + hills + signals + busy-road + non-green
```

Each added component is finite and nonnegative. Resolve UI levels once, keep
raw observations separate, and expose model/settings identity with results.
The minimum total is the physical arc length. The current search validates
nonnegative finite costs, but does not enforce that stronger lower-bound
invariant; enforce it in the composed evaluator and its tests.

The scalar evaluator and breakdown evaluator use the same component functions;
do not maintain duplicate formulas. Produce breakdown objects for selected
routes, avoiding a diagnostic object allocation for every explored arc. A
breakdown's total must match the scalar evaluator and the selected search cost
within a documented floating-point tolerance. Return-leg diagnostics use the
outbound-used set that was fixed for that return search. Do not reconstruct
reuse penalties from the final closed walk alone.

Raw results include estimated ascent/descent and terrain confidence, mapped
crossing passages and unresolved evidence, and environmental exposure/coverage
when supported. Fallback costs do not create fictitious observations. Cost
totals are diagnostic ranking values; physical distance and time have distinct
fields and units.

## Splits, direction, and reuse

`PhysicalPart` currently records split intervals in graph chainage. Reuse that
mapping to identify the original segment; infer forward/backward from the arc
relative to the overlay segment, never from signed grade or identifier spelling.
Unsplit arcs refer to their full original segment. A `TraversalRef` uses ascending
physical start/end chainage plus a separate direction flag.

Slice terrain/context intervals through their graph-chainage mapping, integrating
their corresponding projected spans. Do not treat graph and projected chainage
as interchangeable, or apportion nonlinear hill cost by a whole-segment average.
Reversal negates signed grade and swaps ascent/descent; symmetric context features
remain the same. Signal boundaries retain their event identities through splits.

For the first extraction, keep reuse keyed by overlay segment ID, preserving
current behavior. Both legs share one overlay partition, so those IDs represent
the same physical portions. Do not collapse all split parts to an original way
or segment ID: that would charge disjoint partial traversals as reuse. Different
overlays or future arbitrary route revisions would require explicit physical
interval overlap, and are outside this refactor.

## A*, zero width, and event-dependent costs

Retain the calibrated geometric heuristic. Since every composed arc cost is
at least its stored length, the existing distance lower bound remains admissible
and consistent. A* needs no knowledge of hill/exposure feature extraction.
Compare against zero-heuristic search on small directed fixtures with the same
resolved costs. Strong penalties may weaken the heuristic's usefulness; measure
expanded states before introducing a more complex bound.

Zero loop width disables centre, side, and reuse shape penalties. It must not
disable new route preferences. Keep the single zero-width candidate behavior.
Initially permit the current reverse-path shortcut only when all new preferences
are Off and a legal reverse traversal exists. Search the return whenever a new
preference is enabled; a later proven symmetry check can recover the optimization.
Always score a retraced return with its actual return cost model, rather than
assigning physical length as its cost when preferences are active.

The scalar arc callback is appropriate only for costs fixed for each directed
arc during the search. If signal charging needs arrival/event context, add a
separate stateful search path with a precisely typed state such as node,
incoming arc, and active crossing event. Use the same component formulas for its
transition costs and the same distance lower bound from the state's node.
Do not read mutable "last crossing" state from an arc-cost callback: A* explores
many competing paths, so that would corrupt costs. Carry event context across
the leg boundary when a turnaround lies inside an event; test any candidate
selection consequences explicitly. Final event state and attribution remain a
traffic-light implementation gate, not a prerequisite for extracting current
costs or delivering hills.

Each leg remains least-cost under its fixed model. Outbound-then-return
construction remains greedy; this modularization does not introduce joint
optimization of complete outings.

## Delivery and verification

1. **Extract existing shape costs.** Move shared records/helpers as needed,
   introduce named shape features/strengths and request data/cache, extract
   preparation/evaluation, and replace the planner closure with a bound function.
   Preserve current route geometry, costs, zero-width behavior, eligibility,
   lazy integration, deadline handling, and completed-candidate retention.
2. **Introduce preference contracts.** Add levels, resolved/versioned weights,
   feature capability validation, traversal references, and per-term diagnostics.
   All-Off preserves baseline results and supports existing prepared artifacts.
   Do not add enabled controls for unimplemented features.
3. **Deliver hills.** Integrate signed interval features, apply the audit's
   bounded/uncertain-terrain policy, add independent preference costs, and change
   zero-width return handling. Calibrate the level table on recorded local cases.
4. **Extend with audited signal/context features.** Reuse the composition and
   diagnostic contracts. Introduce stateful transitions only if passage modelling
   requires them, with complete fixtures and leg-boundary semantics.

For extraction, use existing planner/API tests and a fixed-fixture comparison
of both candidate geometries and costs. Focus new checks on cached field sharing,
leg-context isolation, scalar/breakdown agreement, and deadline behavior. For
preferences, test asymmetric forward/reverse costs, partial-span additivity,
all-Off compatibility, invalid/unsupported settings, and finite nonnegative
components with `total >= length`. Compare A* with Dijkstra on small fixtures
and measure local regeneration latency and expanded states. Run commands with
`uv`; frontend checks become necessary when result/API/UI contracts change.

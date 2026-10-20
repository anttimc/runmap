# Building-access dead ends and path rendering

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

Status: implemented. Measurements and validation are recorded in
[the implementation results](24-building-access-results.md). Apply a conservative building-access filter by
default to every prepared path overlay, including gradient colors with no start
selected, distance colors, and reachable-path emphasis. Reduce work on the
server and in the browser by omitting hidden geometry from map responses.

## Current behavior

- `paths/graph.py` retains segment `service`, `access`, and `foot` tags and node
  `entrance` tags. Parking aisles are already excluded during import.
- `routing/search.py` indexes physical segments and directed arcs. Dijkstra's
  predecessor tree is unsuitable for dead-end detection: it omits alternative
  connections, and `shortest_path` stops when its destination is reached.
- `api/paths.py` builds display features and a spatial index for all measurement
  intervals. `viewport_paths` returns excluded paths as contextual geometry;
  `explore_paths` subsequently evaluates the returned intervals.
- `render_paths` merges only contiguous intervals with exactly equal styling
  values. Continuously varying grades and distances can leave many features.
- `web/src/paths.ts` replaces the MapLibre GeoJSON source after viewport requests.
  All colored path layers consume that source. Changing opacity alone would
  retain payload, parsing, and source-processing costs.

## 1. Record a performance and classification baseline

Use fixed urban and forest viewports in the prepared Leppävaara dataset, including
zooms 11, 13, and 15. Cover gradient mode without a start and both coloring modes
with exploration enabled. Save viewport bounds, start, budget, dataset digest,
and machine/browser details so filtered and unfiltered runs are comparable.

Measure cold and warm API latency, candidate and returned interval counts,
rendered feature/vertex counts, response bytes, and time from requesting paths
to the map finishing the source update. Record repeated-run median and p95;
distinguish network/server time from browser source processing and rendering.
Measure classification startup time and additional index memory separately.

## 2. Classify topology once per dataset and access policy

Add `paths/dead_ends.py` with typed data-only records and small module-level
functions. Produce segment classifications with reason, branch identifier,
attachment node, total branch length, and evidence. Preserve original graph IDs
and measurement artifacts.

Build an undirected multigraph of physical segments permitted by public
pedestrian access rules and prepared route boundaries. Keep a segment if at
least one pedestrian direction is legal; directed routing still enforces the
actual permissions. Count parallel segments separately and self-loops twice.
Do not use the existing raw degree map without applying these exclusions.

Use queue-based degree-one peeling to identify dangling trees attached to the
remaining graph, with O(V + E) topology work. Retain attachment and parent
relationships so a terminal access twig can be classified without hiding its
siblings. Treat components that peel away entirely as ambiguous in version one.
Compute across the complete prepared graph, never the current viewport or
distance budget.

Use access topology independent of temporary surface/stair preferences: hiding
stairs must not turn a real through-connection into an alleged building dead end.
Cache classifications by dataset digest, access-policy identity, and classifier
version. Build once at load time or on first use, not for every viewport request.

## 3. Apply conservative building-access rules

Initially classify only terminal twigs or whole dangling trees supported by
the existing tags:

- A terminal chain consisting entirely of `highway=service` and
  `service=driveway` segments is a building-access candidate.
- A short terminal chain of footway/service/steps segments ending at an active
  building entrance is a candidate. Normalize entrance values and exclude
  negative/non-entry values such as `no` and `exit`. Start with a configurable
  150 m total-chain limit, then validate it against mapped examples.
- Stop chain propagation at a branching junction or incompatible road/path type.
  Hide a shared parent only when all its terminal children qualify and the
  parent itself satisfies the access-type rules. One entrance must not hide a
  mixed branch containing a forest trail or an ordinary residential street.
- Protect candidate branches touching the existing conservative 100 m coverage
  boundary band. Treat geometry-envelope-only coverage as uncertain; require
  reliable extract bounds before automatic classification there.
- Preserve unclassified dead ends, through footways, scenic paths, and connectors
  between loops. A generic `highway=service` tag alone is insufficient.

Record `building_access_driveway` or `building_access_entrance` as a visibility
reason, separate from legal access and profile eligibility. This is a running
map preference, not a claim that pedestrians are prohibited.

Single-entry areas containing a turning loop do not peel away. Defer their
classification and building-footprint acquisition until inspection of the first
version demonstrates a need. Existing service and entrance evidence is enough
to implement and evaluate the initial filter.

## 4. Filter before interval processing and serialization

Keep the full routing graph and full snapping index. Build a second display
spatial index referencing only visible intervals; share feature storage rather
than copying coordinates. The default viewport query uses this index, so hidden
intervals never reach geometry intersection checks, exploration interval-state
calculations, `render_paths`, JSON serialization, or MapLibre.

Add one `hide_building_access` option, defaulting to true, to viewport and
exploration requests. Apply the same classification regardless of color metric,
start selection, budget, surface filter, or stairs setting. Include policy
identity and aggregate hidden segment/interval counts in configuration or
diagnostics. Count only delivered features against the viewport feature limit.
Include the option in any response-cache identity; distance-search caches can
remain shared because routing topology is unchanged.

Retain full-graph snapping and traversal for legally accessible starts on hidden
branches. Show the snapped start marker and offset; do not restore the whole
branch to the colored overlay. A selected segment can use the existing selection
overlay and inspection endpoint, which should report its visibility reason.
Legal access restrictions always continue to apply. This also preserves future
explicit destination access without introducing endpoint exceptions into access
rules. Version one targets interval/rendering cost, not Dijkstra pruning.

## 5. Expose one consistent map control

Add a checked “Hide building-access dead ends” control next to the path filters
in `web/index.html`, with short help explaining that it affects all path colors.
Wire it through the shared request parameters in `web/src/paths.ts`. Inspection
can accept the option to explain current visibility. Preserve request cancellation
and sequence checks when toggling. Unchecking restores the original overlay for
comparison; surface and stair behavior remains independently controlled.

The normal basemap may still show these streets faintly: it uses a separate
vector source. Filtering applies to every prepared path overlay, including
quality and stair layers, by removing features from their shared source.

## 6. Validate correctness and rendering improvement

Add focused topology tests for a driveway chain, entrance footway, branched
building access, a mixed building/forest branch, a through street with an entrance,
a scenic dead end, extract-boundary uncertainty, a tree-only component, parallel
segments, a self-loop, a connector between loops, and a turning loop that remains
unclassified. Cover pedestrian restrictions and invariance under surface/stair
display preferences.

API tests must verify that the same classified segment IDs disappear from both
color modes with and without exploration; excluded intervals are not evaluated;
disabling the filter restores them; and starts and inspection on hidden legal
branches still work. Verify that all-hidden viewports return a valid empty ready
response and that feature limits apply after filtering.

Run the relevant existing path, routing, exploration, API, and web tests using
`uv run python -m unittest`, then the frontend build with `pnpm --dir web build`.
Browser-check toggling, panning, switching metrics, selection, and start snapping.
Inspect actual hidden urban examples and retained forest/through paths.

Repeat the baseline and report counts, bytes, timings, and memory deltas. Success
requires consistent filtering across modes, no hidden geometry in default map
payloads, no repeated topology work during panning, and a measured reduction in
urban payload/source processing. Do not assume the reduction will make every
viewport fast: if rendering remains slow, use the measured dominant cost to plan
display simplification, bounded color approximation, or vector-tile delivery as
a separate change. Exact inspection measurements remain the reference.

## Delivery order

1. Baseline and inspect candidate branches on the current dataset.
2. Implement cached classification with synthetic topology tests.
3. Integrate the display index and API filtering, preserving start access.
4. Add the default-on map control and verify every coloring mode.
5. Publish before/after measurements and tune only rules supported by examples.

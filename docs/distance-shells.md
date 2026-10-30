# Distance shells

In **Map layers**, turn on **Distance from start** and choose the shared start
and a forward distance budget (1–50 km, in 1 km steps). Explanations for the
color modes, budget, and smoothing are available from their info popups. Under its **Advanced options**, select **Grid** or
**Filtered triangulation**. Both display filled 500 m bands with thin outlines
over the normal basemap. The final band can be
shorter. The method selector reports generation time so the two methods can be
compared. Clicking a mapped path opens the inspection panel with its original
distance and terrain samples. Inspection uses prepared hit geometry when present
and falls back to network snapping when no path is hit.

The filled space between paths is an approximate distance surface, not evidence
that the intervening ground can be traversed. Neither algorithm has water or
barrier polygons. Both are clipped to prepared coverage. Shell display requires
the SQLite backend and its matching numeric routing artifact; configuration
advertises available methods. Datasets without numeric routing retain gradient
display and inspection; the distance switch is disabled. Distance path colors,
snapshot MVTs, and `/api/paths/explore` have been removed.

Choose **Extra travel (distance)** under **Color the map by** in the main distance controls
to color by extra travel: shortest legal network distance minus the straight-line distance from
the snapped start, clamped to zero. Both distances use EPSG:3067 metres. This
highlights detours around obstacles. The forward budget still limits the search;
it is not an extra-travel budget. Extra-travel bands are 200 m wide and the legend identifies
extra travel; **Distance from start** bands remain 500 m. The extra-travel color scale
and legend end at the largest extra distance among the reached samples, with a
1 m minimum scale for zero-detour results. The last band can be shorter than
200 m. The API reports this scale endpoint as `maximum_m`; standard mode reports
the forward budget. Path inspection continues to report the original network distance.
Subtraction happens at network samples before either surface is interpolated;
samples beyond the forward budget are excluded in this mode.

**Extra travel (%)** shows `100 × max(0, network − direct) / max(direct, 500 m)`, using
straight-line distance with a 500 m denominator floor: a 1.5 km path to a point 1 km away is
50% extra travel. At the snapped start (direct distance ≤ 0.000001 m), the value
is zero. Percentage is calculated at reached samples before smoothing and
interpolation. The floor suppresses unstable ratios near the start; percentages
at least 500 m away are unchanged. Extra travel in metres is unaffected.
The percentage scale ends at the 99th percentile of finite smoothed Grid values,
or the unique vertices of retained triangles (minimum scale endpoint 1%). It
covers the explored surface before coverage clipping, independently of viewport.
Band widths are whole percentage points, chosen to produce at most 15 bands.
Values above the cap use the top color and remain shaded. Grid values are
saturated after smoothing; triangulation values are saturated at mesh vertices
before contouring. This display cap does not change cached samples or percentage
calculations. Distance mode retains its 200 m bands. Both modes use the same network
budget and preserve exact network distances in inspection.

## Shared distance samples

Both methods use the existing forward-Dijkstra snapshot. For a segment of length
`L`, samples at graph position `s` use the minimum legal value from the forward
anchor `d(source) + s`, backward anchor `d(target) + L - s`, and the snapped start
when it lies on that segment. Forbidden directions contribute no distance.
Sampling uses the full projected polyline and its scaled chainage, rather than
the chord between endpoints. The default sample spacing is 25 m.

To resolve the final budget boundary, samples extend up to 300 m beyond the
budget on edges anchored at reached nodes. This does not run a second search or
imply additional reachable nodes. Geometry comes directly from immutable numeric
chunks; display measurements and path vector-tile encoding are not needed.

## Grid

The default grid spacing is 50 m. A KD tree assigns each grid point its nearest
sample's network distance within 150 m; points farther from samples stay masked.
Before contouring, **Contour smoothing** in Advanced options offers Off, Light
(default, 25 m Gaussian sigma), and Strong (50 m sigma). Smoothing operates on
the displayed metric, after straight-line subtraction for Extra travel. Gaussian
weighted values are divided by Gaussian valid-cell weights; the original mask
is restored, so missing areas remain missing. It can blend nearby values across
narrow gaps and soften real barrier-related jumps. It does not change routing,
inspection, the budget, or the color scale based on unsmoothed samples. Mask and
coverage edges can still look angular. All bands share the same smoothed surface.

ContourPy extracts filled bands between consecutive 500 m thresholds. Zero-area
rings at exact threshold extrema are removed. Holes and separate components are
preserved, with no independent simplification of adjacent rings.

## Filtered triangulation

Samples within 50 m of any shell threshold retain one minimum-distance
representative per occupied 50 m cell. Away from thresholds, minimum and maximum
distance anchors remain in 200 m cells. The final requested boundary also gets
dense samples when it is not a multiple of 500 m.

SciPy/Qhull triangulates those sites. Triangles with any edge longer than 300 m
are masked to limit large bridges across gaps. Within each remaining triangle,
distance is linear: the triangle is clipped against the lower and upper band
levels, then the resulting fragments are merged with Shapely. Shared edge
crossings use canonical endpoint ordering; millimetre precision removes
floating-point slivers. Interior rings survive the merge. Constant-threshold
triangles belong to one band, avoiding overlapping filled areas.

Triangulation is not smoothed. Its smoothing control is disabled, but the selected
setting is used if sparse geometry falls back to Grid.

The API does not depend on Matplotlib. The earlier triangulation benchmark used
Matplotlib's public contour API; its contour-only timings are not timings for
the delivered polygon assembly implementation. Very sparse or effectively
one-dimensional geometry falls back to the grid. The response and UI identify
this fallback. Point reduction and interpolation are approximate, so the two
methods need not produce identical boundaries.

## Delivery and limits

Prepare an exploration with `POST /api/paths/snapshots`, then request:

```text
GET /api/paths/snapshots/{snapshot_id}/shells?dataset_id={dataset_id}&method=grid
GET /api/paths/snapshots/{snapshot_id}/shells?dataset_id={dataset_id}&method=triangulation
```

`subtract_straight_line=true` enables extra-travel bands (default `false`); the
response echoes this flag. Switching it reuses cached search and network samples,
with separate encoded response cache entries.

`smoothing_m` selects 0, 25 (default), or 50 m Gaussian sigma. Responses echo
`smoothing_m` and report `smoothing_used_m` (zero for effective triangulation).
Smoothing changes reuse sampled geometry, with separate response cache entries.

`extra_travel_percentage=true` selects percentage and implies straight-line
subtraction. The response echoes the flag and supplies `unit` (`m` or `percent`),
`maximum`, and `interval`. Existing `maximum_m`, `interval_m`, `lower_m`, and
`upper_m` fields are compatibility aliases whose numeric values use the reported
unit, including percentages in percentage mode. Percentage requests have separate
encoded cache entries while sharing network samples with the other modes.

`cell_m` optionally selects 25, 50 (default), or 100 m; the UI uses 50 m. The
response is a GeoJSON FeatureCollection of MultiPolygons with `lower_m` and
`upper_m` properties, plus dataset/snapshot identity, requested/effective method,
resolution, vertex count, and generation timings. Shells follow the snapshot's
policy and budget. Building-access visibility is a path display filter and does
not change routing or shells.

Sampling has a 64 MiB TTL/LRU cache shared by both methods. Encoded responses
have a separate 32 MiB cache. Identical pending work coalesces; oversized results
can be served without retention. Cache lifetimes follow snapshot TTLs, and the
endpoint validates the snapshot on every request. Responses have private cache
control and ETag/304 support. Expired snapshots return HTTP 410; the frontend
re-prepares once. Method changes reuse the current search and sampled geometry.

At most two shell builds run per service, with a 20-second generation deadline,
a four-million-cell grid limit, and a 400,000-site mesh limit. Numeric payloads
are read under the pinned artifact's lock, which is released before sampling,
surface generation, and polygon assembly. Sample buffers are sealed read-only.

The frontend uses one GeoJSON source with fill/outline layers below basemap
roads and labels. Background gradient, Path gradient, and Distance from start
have independent visibility controls. Path hit testing remains available, and
path tiles may be requested while distance shading is on. Background gradient
opacity is reduced to 30% of its selected value while the distance layer is on.
The shared start also drives run planning; the distance budget applies only to
distance exploration. Changing inputs aborts prior fetches and clears stale shell
data; sequence checks prevent late responses from replacing the current result.

## Verification

Unit/API tests cover directed and partial-start distances, extra boundary
support, clipped band partitions and holes, degenerate geometry, coverage
clipping, cache coalescing, immutable samples, request identity, expiry, and
HTTP validators. Frontend tests cover transport, expiry errors, matching legend
colors, and layer/source behavior.

The browser script below predates the independent map-layer controls. Its UI
selectors and zero-path-tile assertion require updating before it can validate
the current UI. Current browser acceptance is tracked in the
[roadmap](roadmap.md#next-correctness-and-local-acceptance).

```sh
# Vite on 5173, API on 8000, Chrome remote debugger on 9231
cd web
node scripts/check-distance-shells.mjs ../artifacts/distance-shells/browser
```

The script saves screenshots and observations in that artifact directory. Earlier
algorithm measurements and reproduction commands are retained in the
[prototype report](archive/history/53-distance-shells.md).

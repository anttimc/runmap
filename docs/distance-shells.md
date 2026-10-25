# Distance shells

In **Explore → Distance from start**, choose a start and a forward distance
budget, then select **Grid** or **Filtered triangulation**. Both display filled
500 m bands with thin outlines over the normal basemap. The final band can be
shorter. The method selector reports generation time so the two methods can be
compared. Clicking a mapped path snaps to the network and opens the existing
inspection panel, including its original distance and terrain samples.

The filled space between paths is an approximate distance surface, not evidence
that the intervening ground can be traversed. Neither algorithm has water or
barrier polygons. Both are clipped to prepared coverage. Shell display requires
the SQLite backend and its matching numeric routing artifact; configuration
advertises available methods. Datasets without numeric routing retain gradient
display and inspection; the distance button is disabled. Distance path colors,
snapshot MVTs, and `/api/paths/explore` have been removed.

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
roads and labels. Distance mode hides all path vector layers, including hit
testing, so it does not request path tiles. Inspection instead uses the snap
endpoint. Changing inputs aborts prior fetches and clears stale shell data;
sequence checks prevent late responses from replacing the current result.

## Verification

Unit/API tests cover directed and partial-start distances, extra boundary
support, clipped band partitions and holes, degenerate geometry, coverage
clipping, cache coalescing, immutable samples, request identity, expiry, and
HTTP validators. Frontend tests cover transport, expiry errors, matching legend
colors, and layer/source behavior.

The browser check exercises actual MapLibre rendering, both methods, snapshot
reuse, zero path tile requests in distance mode, click inspection, rapid budget
changes, clearing the start, and returning to gradient mode:

```sh
# Vite on 5173, API on 8000, Chrome remote debugger on 9231
cd web
node scripts/check-distance-shells.mjs ../artifacts/distance-shells/browser
```

Screenshots and browser observations are saved in that artifact directory. The
earlier algorithm measurements and reproduction commands are retained in the
[prototype report](archive/history/53-distance-shells.md).

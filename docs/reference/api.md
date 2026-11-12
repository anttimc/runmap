# API and request contracts

FastAPI's `/docs` provides live request and response schemas. This reference
describes endpoint roles, identities, bounded search, and result lifetimes.
See [architecture](../architecture.md) for the service boundaries and
[development](../development.md#configuration) for deployment settings and defaults.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/config` | Terrain coverage, manifest, legend, and tile templates |
| `GET /api/environment/config` | Local environment source manifest, pilot/query boundary geometry, and checksum-bound feature URL |
| `GET /api/environment/features.geojson?dataset={sha256}` | Published pilot source features; 404 if unavailable, 409 if the manifest identity changed |
| `GET /api/environment/rasters/config?source={source_sha256}` | Prepared raster manifest, image template and sample URL; 404 if absent, 409 for source mismatch |
| `GET /api/environment/rasters/{dataset}/{layer}.png` | Fixed baseline preview: forest, park, water, traffic, wetland, or evidence_gap; rejects unknown layers and stale identities |
| `GET /api/environment/rasters/sample?dataset={dataset}&longitude={lon}&latitude={lat}` | One metric raster cell, support/gap channels and nearby source evidence; scores unavailable outside the core |
| `GET /api/regions/{region_id}/terrain/v{version}/{manifest_id}/w{wavelength}/{z}/{x}/{y}.png` | Versioned terrain tiles |
| `GET /api/regions/{region_id}/contours/v{version}/{manifest_id}/{z}/{x}/{y}.mvt` | Source-elevation contour tiles |
| `GET /api/paths/config` | Dataset, coverage, policy options, tile templates, and planner/shell capabilities |
| `GET /api/paths/signals/config` | Source-signal overlay availability, network identity, and minimum map zoom |
| `GET /api/paths/signals?dataset={id}&west={lon}&south={lat}&east={lon}&north={lat}` | Bounded source-signal GeoJSON points, separate from route passage counts |
| `POST /api/paths/run/profile` | Absolute terrain elevation profile for ordered route traversals |
| `GET /api/paths/snap` | Eligible endpoint snap and nearby alternatives |
| `POST /api/paths/snapshots` | Prepare a forward-distance exploration snapshot |
| `GET /api/paths/snapshots/{snapshot_id}/shells` | Distance shells as GeoJSON |
| `GET /api/paths/snapshots/{snapshot_id}/turnaround` | Estimated target-distance turnaround region as GeoJSON |
| `GET /api/paths/tiles/v{version}/{dataset_id}/static/{policy}/{variant}/{z}/{x}/{y}.mvt` | Static path-gradient tiles |
| `POST /api/paths/run` | One-way or round-trip planning with resolved costs and search metadata |

`GET /api/paths/viewport` is a diagnostic GeoJSON path query. `/api/terrain.png`
serves the single-raster override. Configuration responses advertise currently
supported capabilities; clients should not infer them from basemap coverage.

## Route elevation profiles

`POST /api/paths/run/profile` accepts `dataset_id` and `legs`, an ordered list
of one or two traversal lists. Each traversal contains `segment_id`, `start_m`,
and `end_m` in graph chainage, as returned by run planning. Decreasing offsets
mean reverse travel. Partial and repeated traversals retain their travel order.
Requests are bounded to 5,000 traversals, 200 km, and 30,000 profile intervals;
unknown segments return 404, invalid offsets or disconnected traversals return
400, and stale datasets return 409.

The response includes dataset/terrain manifest identity, smoothing wavelength,
sampling interval, total distance, optional turnaround distance, ascent/descent,
coverage fraction, completeness, and intervals. Each interval has cumulative
start/end distance, nullable absolute start/end elevations and signed grade,
coordinates, and quality flags. Samples use bilinear interpolation on prepared
height rasters; missing tiles/nodata produce gaps. Totals sum valid sampled
height differences, including uncertain ground beneath structures. They are
independent of hill preferences and their gradient-derived cost observations.
The server reads selected segment records and height windows without a routing
search. The browser caches at most eight profiles per dataset/controller and
rejects superseded results. The former segment-inspection endpoint is removed.

## Identity and caching

Requests that identify a path dataset must match the active dataset. A mismatch
returns HTTP 409. Terrain URLs identify region, manifest, rendering version, and
smoothing; path-tile versions incorporate prepared feature identity. Unknown
rendering versions return HTTP 404. Versioned tiles use immutable public caching;
path tiles support ETag/304 validation.

Exploration snapshots are process-local, with bounded retained bytes and TTL.
Snapshot preparation responses use `no-store`; shell responses use private caching
limited by snapshot expiry and support ETag/304. Expired or evicted snapshot IDs
return HTTP 410. The browser prepares a replacement once on expiry.

## Run planning

`preferences.signals` accepts Off/Low/High when the SQLite dataset has a matching
prepared signal sidecar. Configuration advertises its supported levels and
versioned equivalent-metre weights. Without that artifact only Off is supported;
non-Off requests return 400. Leg breakdown `signals` includes both resolved and
unresolved candidate penalties. Observations `signal_passages` and
`unresolved_signal_passages` count occurrences, including repeats; both are null
when the preference is Off. Zero recognized passages does not prove the absence
of lights. The explicit JSON service does not discover sidecars; the reference
SQLite planner supports them. See [traffic-light avoidance](../traffic-light-avoidance.md).

The source-signal map requires sidecar schema 2. Points expose OSM IDs, source tags,
and `kind` (`pedestrian` or `uncertain`), including nodes outside the runnable graph.
Missing map evidence returns 404, a stale network identity 409, and invalid bounds
422. Viewports over 2,500 km² or 5,000 points return `status=zoom_in` with no
features. The browser additionally requires zoom 13 and rejects stale/cancelled
viewport responses. A source node is not a distinct wait or proof of a charged
route movement.

The request accepts `mode: "one_way" | "round_trip"`; omission defaults to
`round_trip`. Endpoint fields remain `home` and `turnaround`. For one-way planning
they mean start and destination. Endpoints must snap to distinct eligible points.
Surface/stair policies and supported preferences apply in both modes.

Responses and candidates report their mode. Each candidate contains ordered
`legs`: one outward leg for one way, or outward then return for a round trip.
Each leg retains coordinates, distance, cost/breakdown, preference observations,
expanded-node count, and ordered physical traversals. A traversal identifies its
original segment and directed start/end graph chainages.

The leg cost breakdown also includes `environment_greenery`,
`environment_water`, `environment_traffic`, and `environment_wetland` in
equivalent metres. Public requests currently leave all four at zero because
environment preference controls are not enabled.
When joined environment data is present, observations report
`environment_unknown_greenery_m`, `environment_unknown_water_m`,
`environment_unknown_traffic_m`, and `environment_unknown_wetland_m` as graph
metres, including fields whose weight is Off. These are null without joined data.

One-way candidates have null orientation and centre diagnostics. Their effective
shape settings have zero width, and centre/side/reuse strengths are zero. They
require no return connection. Round trips retain repeated-distance and shape
diagnostics; zero width still requires a complete outward and return route.
Equivalent round-trip candidates may be marked with `equivalent_to`.

| Run status | Meaning |
| --- | --- |
| `complete` | A complete candidate was found for the requested route mode |
| `unreachable` | No complete route was found in the settled working area |
| `no_eligible_snap` | At least one endpoint could not be snapped under the selected policy |
| `deadline` | The shared deadline expired; completed candidates may remain available |
| `area_limited` | Search-area admission or expansion limits were exhausted |

An unavailable second round-trip side assignment does not invalidate a completed
candidate. Outcomes record assignment-level completion/failure. Cost units,
preferences, and observations follow the
[routing mathematics](routing-mathematics.md).

## Bounded search areas

Display queries and routing windows have different purposes. A browser viewport
does not define legal routing coverage. Routing loads complete candidate segments
and reports unloaded-frontier evidence separately from source-coverage proximity.

**Exploration** starts from the full snapped segment's projected envelope,
expanded in each direction by the forward budget divided by the dataset-wide
distance scale, plus one metre. A reached unloaded frontier causes a retry with
both box dimensions doubled. Attempts share one preparation deadline and area,
segment-count, and retry limits.

**Planning** starts with a square at the endpoint midpoint. Its side is 1.5 times
straight-line endpoint separation, subject to the configured minimum dimension.
Reached unloaded frontiers, candidate geometry near the query boundary, or
incomplete assignments with unloaded connections can trigger box doubling.
The proximity band is the larger of the configured minimum and 5% of the shorter
box dimension. All candidate searches rerun against the same expanded working set;
legs from discarded attempts are not combined.

Expansion is heuristic: a better route outside the loaded area can remain
undetected. Run metadata reports attempted bounds, counts, retry reasons, frontier
and boundary involvement, and source-coverage limits. Admission uses bounded
source-segment counts before graph decoding; counts include source candidates
excluded by the active policy. Exact limits/defaults are in
[configuration](../development.md#configuration).

## Work and result lifetimes

Dataset services own read-only database connections and shared immutable numeric
working sets. Searches own endpoint overlays, traversal state, cost context, and
partial-endpoint hill detail. Hills, environment fields, and signals share the
[prepared input boundary](../architecture.md#prepared-routing-inputs).
Numeric working sets are cached by bounds and environment/signal channel presence;
different bounds also reuse resident immutable chunks and their validated native
topology through the same dataset service's shared graph store. Store loading is
serialized by the existing preparation lock. Eviction and database shutdown do
not invalidate chunk owners retained by active graphs. Area-wide node lookup,
selection, and frontier state are still assembled for new bounds.
With an environment preference enabled, a numeric working set retains the selected
physical-segment intervals. With all environment preferences Off, only returned
route segments load those inputs, for unchanged unknown-length observations.
Signal search data loads only with signal avoidance enabled; Off keeps absent
passage counts. Hills load endpoint detail only when enabled, using directional
chunk totals for whole segments. Distance exploration loads neither optional
environment/signal search data nor hill endpoint detail. Capabilities still reflect
configured validated source availability.

Numeric exploration snapshots retain independent sparse reached-node identities
and distances. They survive graph-cache eviction without retaining native graph
owners. Reference snapshots retain the local graph and its distance buffers
together. Static gradient tiles are independent of exploration snapshots; shell
generation reuses the snapshot search.

Encoded path tiles, compact display inputs, exploration snapshots, and numeric
working sets have separate cache/accounting lifetimes. These cache budgets do not
cap total process RSS or the OS page cache. Identical pending cache work can be
coalesced. Terrain tile caching is entry-bounded; missing terrain derivatives are
generated synchronously under a process-local generation lock. Shell-specific
sampling, cache, and worker limits are in [distance shells](../distance-shells.md).

Path database connections are leased exclusively across request threads. Tile
reads use bounded batches and release leases before styling and encoding. Planner
admission slots bound concurrent preparation/search. One planning deadline spans
snapping, connection waits, loading, retries, and search. Native graph construction
and traversal release the interpreter lock; the scalar integration adapter used
by reference planning retains it during each call.

Browser aborts stop waiting and generation guards suppress stale results. They do
not immediately terminate backend work. Snapshots and caches are not shared across
API processes; use the current single-worker deployment described in
[development](../development.md#install-and-run).

## Errors outside run results

| HTTP status | Examples |
| --- | --- |
| 400 | Invalid settings or unsupported preferences |
| 409 | Dataset identity mismatch |
| 410 | Expired or evicted exploration snapshot |
| 422 | Request-schema validation; exploration area limits include explicit `area_limited` metadata |
| 429 | Planner admission slots are busy |
| 503 | Path data unavailable or database connection leases exhausted |
| 504 | Exploration preparation or shell generation deadline expired |

Run deadline/area outcomes are returned in the run response rather than being
reported as an unreachable destination. Missing source connections remain a
coverage limitation, independently of query-area exhaustion.

## Turnaround shading

`GET /api/paths/snapshots/{snapshot_id}/turnaround` takes `dataset_id`, `target_m`
(500–40000, finite), and `width` (0–0.6, default 0.18). It requires a numeric
snapshot with a distance budget at least 115% of the target. It returns a GeoJSON
FeatureCollection with `dataset_id`, `snapshot_id`, `target_m`, `width`,
`tolerance=0.05`, and `model="cubic-extra-v1"`. Features have `core` (boolean)
and `region` (`target` or `uncertainty`). The former `lower_m`/`upper_m` properties
are no longer returned: uncertainty contours do not represent fixed relative
target-error bands. An empty collection is a valid result.

`uncertainty` contains `method="held-start-residual-v1"`, `nominal_mass=0.8`,
`residual_per_separation=[lower,upper]` for the requested width,
`sampled_separation_m=[1500,10000]`, and `independently_calibrated=false`.
Residuals have units of route metres per metre of direct separation. Bounds are
linearly interpolated across width knots. The light region marks overlap between
the empirical predicted-length interval and the target ±5% interval; it is not a
fixed ±15% band or a guaranteed coverage statement. At width zero only the core
is emitted. Model/calibration versions participate in encoded-response caching.

Dataset mismatches return 409, expired snapshots 410, invalid inputs or insufficient
budget 400, and deadlines 504. Responses use private revalidation; the server
caches encoded results and round-trip samples within the shell service's bounded
caches. Snapshot expiry is checked even on cache hits. This endpoint uses
surface/stair eligibility but does not model hill preferences or orientation.
See [distance shells](../distance-shells.md#estimated-turnaround-region) for UI
behavior and approximation limits.

## Environment map tiles

`GET /api/environment/tiles/config` returns the independent completed regional
bundle identity, bounds, layers, colors, limitations, and versioned XYZ template.
An absent bundle returns 404 without disabling terrain or routing.

`GET /api/environment/tiles/v1/{dataset}/{layer}/{z}/{x}/{y}.png` serves 256 px
Web Mercator tiles for `greenery`, `water`, `traffic`, `wetland`, and `support`.
Zooms 8–16 are supported, with browser overzoom above 16. Invalid coordinates return
422; unknown layers/render versions return 404; stale dataset identities return
409. Successful tiles have a one-day immutable browser cache policy. Server reuse
holds at most 128 PNGs, keyed by bundle path, dataset, file modification time,
layer, and XYZ. Requests read only relevant TIFF blocks through a fixed-size
warped window; analytical fields are never recalculated on a request.

Display uses nearest-neighbor resampling to retain analytical cell values and
support masks. Score alpha runs from transparent at zero to 210/255 at one;
independent browser opacity applies afterwards. Unsupported or missing cells and
areas beyond the prepared footprint are transparent. The `support` layer adds a
subtle gray tint to prepared cells and amber hatching where field/road context is
limited. Hatching uses global tile-pixel coordinates to avoid pattern seams.
These tiles are colored displays, not numerical inputs for routing or sampling.

## Local Suunto export

The optional `/api/integrations/suunto` API accepts loopback access only. Browser
origins must match the backend or a local Vite origin at port 5173. Writes require
`X-Runmap-Action: suunto` and JSON bodies. Unconfigured operations return 503;
untrusted origins/hosts/clients or a missing action header return 403.

| Endpoint | Contract |
| --- | --- |
| `GET /api/integrations/suunto` | `{configured: boolean}`; configuration presence does not establish login success |
| `POST /api/integrations/suunto/imports` | `{operation_id: UUID, name: string, gpx: string, use_in_watch: boolean = true}` |
| `GET /api/integrations/suunto/imports/{id}` | Read durable status, or 404 if unknown |
| `POST /api/integrations/suunto/imports/{id}/select` | Explicitly select an existing verified creation; empty JSON body |

Operations return `{id, name, use_in_watch, state, error}` with `Cache-Control: no-store`.
`use_in_watch` records the original export preference. States are `running`, `failed`,
`post-pending`, `created-unverified`, `created`, `selection-pending`, or `selected`.
`error` is a sanitized category or null. Receipt state takes precedence over a
process error: a selection error may coexist with `created`.

Names must contain non-whitespace text and be at most 200 characters. GPX is
limited to 2 MiB UTF-8 and the importer's single-track running profile with
2–10,000 points. Validation occurs before login and writes. Reusing a UUID with
different input returns 409. Identical completed/interrupted submissions return
the existing result and never launch another creation. Concurrent writes on the
same operation return 409; retrieve status to recover. Different operations may
run concurrently. Requests wait for the subprocess, with a 90-second deadline;
timeout is recorded in `error`, while any creation receipt is retained.

Only `created` and `selection-pending` permit a selection retry; `selected` is a
no-op. Other states return 409. `post-pending` and `created-unverified` never
permit automatic recreation or selection. Account-side selection does not confirm
physical watch delivery. Operation metadata/GPX/results and importer receipts are
private durable local files, independent of routing snapshots and caches.

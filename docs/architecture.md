# Architecture

Runmap is a local Python processing/API package and a TypeScript/MapLibre web
application. Numerical work uses NumPy, SciPy, and Rasterio/GDAL; the optional
web dependencies add FastAPI, Shapely, ContourPy, Pillow, and an MVT encoder.
Vite builds the frontend. SQLite catalogs elevation products; path artifacts
are JSON files loaded into memory. There is no application database server.

## Data flow

```text
MML elevation -> validated source COGs + immutable coverage manifest
             -> haloed smoothing and differentiation -> derived terrain COGs
             -> PNG terrain tiles / source-elevation contour MVTs

OSM extract -> directed pedestrian graph -> sampled gradient intervals
            -> graph.json + content-addressed measurements
            -> path MVTs, inspection, forward-distance queries, run planning
```

Preparation is explicit. Browser panning reads local prepared data; it does not
download new OSM or elevation coverage. Missing non-default terrain derivatives
are still generated synchronously per analysis core on request. Background jobs
and hosted storage are [future work](roadmap.md).

## Components

| Module | Responsibility |
| --- | --- |
| `sources/` | MML and OSM acquisition/provenance |
| `regions.py`, `elevation/` | Region validation, source grid, raster inspection, catalog, and manifests |
| `terrain/` | Replaceable smoothers and metric-aware derivatives |
| `paths/graph.py`, `paths/terrain.py`, `paths/measurements.py` | Graph preparation, terrain sampling, artifact identity, and profile inspection |
| `paths/exploration.py`, `routing/profiles.py` | Shared eligibility, snapping, temporary start overlay, and forward-distance inclusion |
| `paths/dead_ends.py`, `paths/flat.py` | Building-access visibility and flat-stretch display metadata |
| `routing/search.py`, `routing/run_planner.py` | Generic directed search and two-leg candidate orchestration |
| `routing/cost_*`, `routing/costs.py`, `routing/hills.py`, `routing/fields.py` | Explicit request/leg data, settings, features, integration, and cost composition |
| `routing/baseline.py`, `experiments/` | CLI anchor baseline and standalone measurements/experiments |
| `api/` | Terrain/path delivery, inspection, snapshots, and bounded planner requests |
| `web/src/main.ts`, `paths.ts`, `runs.ts`, `layout.ts` | Map setup, exploration, planner, and responsive controls |

Keep calculations independent of HTTP and rendering. Use data-only records and
focused module functions; behavioral classes are appropriate for reusable
spectral state, stores, and cache/service lifetimes. The planner binds a scalar
arc-cost callback with explicit request data and fixed leg context; it does not
require a plugin registry or a second graph representation.

## Terrain storage and identity

The [Leppävaara region configuration](../config/regions/leppavaara.json) covers
EPSG:3067 bounds `[360000, 6660000, 400000, 6700000]`. Elevation uses a shared
5 km grid at 2 m resolution. Its 64 analysis cores require 100 source tiles with
the 512 m processing halo. Each haloed job processes a 3012 × 3012 grid and
retains the 2500 × 2500 core.

The catalog records acquisition batches, source tiles, coverage manifests and
their tile mappings, active region state, and derived tiles. Matching ready
sources are reused. Manifests identify exact tile versions/checksums; generated
products use source identity and algorithm/settings versions for cache identity.
The source raster/provenance and manifest are retained separately from derived
products. No automatic disk LRU eviction or inter-process acquisition lock is
implemented by the current elevation store.

Derived terrain COGs store UInt16 magnitude and uphill bearing with scales
0.0001 rise/run and 0.01 degree; 65535 denotes nodata. Path sampling reconstructs
gradient components from this quantized product. These are terrain estimates,
not direct samples of absolute smoothed path elevation. Numerical definitions,
boundary behavior, and quantization are in the
[terrain reference](reference/terrain-mathematics.md).

## Path preparation and policy

The [path experiment configuration](../config/routing-experiments/leppavaara-flat-10k.json)
selects a smaller Leppävaara/Kilo extract. Import keeps real shared-node
connectivity: geometric crossings create no junction. Ordinary vehicle one-way
rules do not constrain pedestrians; explicit pedestrian direction rules do.
Cycleways require explicit pedestrian permission. Outdoor pedestrian areas use
perimeters; unsupported/indoor geometry and parking aisles are excluded.

Physical segments own geometry; directed arcs reference them. Shared profile
helpers enforce public access, node restrictions, surfaces, stairs, and prepared
route boundaries in exploration, the UI planner, and the anchor baseline.
Terrain confidence does not remove a legal connection. Building-access hiding
uses conservative dangling-tree classification for display only; full routing
and snapping retain those segments.

Preparation samples at most every 10 m per geometry edge using the configured
40 m terrain product. Ordered intervals preserve grade, height change, validity,
quality flags, geometry, and both projected and graph chainage. Graph distances
are geodesic; terrain integration uses EPSG:3067 lengths. Partial traversals map
between those chainages rather than assuming equal units imply equal lengths.

`graph.json` references `measurements-<sha256>.json`, including checksum and
size. It is published last; earlier measurement generations remain readable.
Loading checks checksum and graph identity. The service builds spatial/display
indexes and directional hill features once per dataset load. Path API identities
and rendering URLs prevent stale datasets or changed measurements being reused.

## Delivery and request lifetimes

Terrain tiles are 256-pixel Web Mercator PNGs, reprojected directly from derived
COGs. Contours use source elevation, ContourPy, and a separate MVT encoder.
Terrain/contour cache lookups share duplicate in-flight work while permitting
distinct tile renders; derived-core generation has a separate process-local lock.

Path MVTs use buffered clipping, zoom-dependent simplification and grade/distance
bins, while keeping segment IDs, eligibility, quality, and inclusion boundaries.
Inspection retains full measurements. Static tiles use immutable versioned URLs.
Start/policy/budget queries prepare process-local snapshots; navigation reuses
tiles without repeating distance searches. Snapshots and encoded tiles have
byte-bounded TTL/LRU caches. Expired snapshots return 410; the client recreates
once. Use one API worker until snapshots can be shared or reconstructed.

Run requests split both snapped endpoints in one temporary overlay. The planner
uses A* with a calibrated graph-distance lower bound, lazy shared shape integrals,
directed hill features, and fixed return-leg reuse context. Costs are finite,
nonnegative, and at least physical length. Legs are optimized sequentially;
there is no globally optimal loop guarantee. Request caches do not persist
across requests. See the [routing reference](reference/routing-mathematics.md).

The synchronous API permits two concurrent planners and applies a cooperative
deadline across preparation/search. Browser cancellation prevents stale result
display; it does not terminate Python work immediately. Client generation guards
protect inspection, snapshots, and runs from obsolete responses.

## API contracts

| Endpoint | Purpose |
| --- | --- |
| `GET /api/config` | Terrain region, manifest, presets, legend, and tile templates |
| `GET /api/regions/{region_id}/terrain/v{version}/{manifest_id}/w{wavelength}/{z}/{x}/{y}.png` | Versioned terrain tiles |
| `GET /api/regions/{region_id}/contours/v{version}/{manifest_id}/{z}/{x}/{y}.mvt` | Source-elevation contours |
| `GET /api/paths/config` | Dataset, coverage, filters, path tile template, and planner capabilities |
| `GET /api/paths/segments/{segment_id}` | Full measurement/profile inspection |
| `GET /api/paths/snap` | Eligible endpoint snap and alternatives |
| `POST /api/paths/snapshots` | Forward-distance snapshot preparation |
| `GET /api/paths/tiles/v{version}/{dataset_id}/…` | Static or snapshot path MVTs |
| `POST /api/paths/run` | Bounded run generation with resolved cost model and diagnostics |

Legacy viewport/explore GeoJSON endpoints remain for diagnostics, and
`/api/terrain.png` is available with the single-raster override. Live request
schemas are available at FastAPI's `/docs`; configuration responses advertise
capabilities and tile versions. Environment variables are documented in
[development](development.md).

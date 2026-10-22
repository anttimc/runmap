# Architecture

Runmap is a local Python processing/API package with a Rust integration kernel
and a TypeScript/MapLibre web application. Numerical work also uses NumPy, SciPy,
and Rasterio/GDAL; the optional
web dependencies add FastAPI, Shapely, ContourPy, Pillow, and an MVT encoder.
Vite builds the frontend. SQLite catalogs elevation products. The path API still
loads JSON artifacts into memory; an initial SQLite path exporter/window reader
is available for the selective-loading migration. There is no application
database server.

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
| `paths/database.py` | Offline SQLite export, read-only metadata access, and projected bounding-box network windows |
| `paths/exploration.py`, `routing/profiles.py` | Shared eligibility, snapping, temporary start overlay, and forward-distance inclusion |
| `paths/dead_ends.py`, `paths/flat.py` | Building-access visibility and flat-stretch display metadata |
| `routing/search.py`, `routing/run_planner.py` | Generic directed search and two-leg candidate orchestration |
| `routing/integer_graph.py` | Prepared dense node/segment/arc indices and shared sparse endpoint overlays |
| `routing/arc_storage.py`, `routing/serialization.py` | Packed arc columns, lazy external record views, and explicit JSON boundaries |
| `routing/coordinates.py`, `routing/geometry.py` | Packed WGS84/projected coordinates, read-only segment views, and sparse overlay geometry |
| `routing/cost_*`, `routing/costs.py`, `routing/hills.py`, `routing/fields.py` | Explicit request/leg data, settings, features, integration, and cost composition |
| `rust/src/fields.rs`, `rust/src/bindings.rs` | Scalar integration kernel and its PyO3 adapter, built as `runmap._routing_native` |
| `routing/baseline.py`, `experiments/` | CLI anchor baseline and standalone measurements/experiments |
| `api/` | Terrain/path delivery, inspection, snapshots, and bounded planner requests |
| `web/src/main.ts`, `map-style.ts` | Map startup, terrain controls, and declarative basemap style |
| `web/src/app.ts`, `state.ts` | Shared home/path policy, mode switching, and map-click dispatch |
| `web/src/api.ts`, `api-types.ts` | HTTP transport, error handling, and frontend request/response types |
| `web/src/paths.ts`, `runs.ts`, `layout.ts` | Feature interaction/rendering and responsive controls |

Keep calculations independent of HTTP and rendering. Use data-only records and
focused module functions; behavioral classes are appropriate for reusable
spectral state, stores, and cache/service lifetimes. The planner binds a scalar
arc-cost callback with explicit request data and fixed leg context; it does not
require a plugin registry. Its integer search view references the same physical
segments and directed arc records as the serialized graph.

The frontend coordinator owns shared state and notifies exploration/planning
controllers when inputs or mode change. Features read shared state and request
home changes through a callback; they do not modify each other's controls.
Exploration mode is held in TypeScript rather than hidden form controls. The
shared API module preserves cancellation and reports HTTP failures consistently;
its response types are compile-time contracts, not runtime schema validation.
DOM lookups use one helper that reports missing markup during setup. Prettier
formats frontend sources; strict TypeScript checks also reject unused code.

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

The default [capital-area configuration](../config/routing-experiments/helsinki-capital-area.json)
uses the elevation region's full 40 × 40 km footprint. The original
[Leppävaara/Kilo experiment](../config/routing-experiments/leppavaara-flat-10k.json)
remains available as a smaller benchmark extract. Import keeps real shared-node
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

The loaded measurement document is packed and released. `MeasurementData` shares
the integer graph's physical-segment ID tuple and lookup; segment offsets locate
interval ranges. Read-only float64 columns retain both chainages, signed grade,
height change, and exact WGS84 coordinates. Separate masks preserve validity and
independent null values; pooled flag tuples preserve flag ordering and unknown
flags. Geometry offsets retain the file's interval vertices without reprojection.
Inspection materializes records for one segment. Hill preparation reads one
segment at a time from the same store. Loading still parses the source JSON and
temporarily holds its dictionaries; this is not a persistent prepared-array format.

## SQLite storage foundation

`paths export-sqlite` converts a published graph and its optional measurement
sidecar into a versioned SQLite database. Export verifies measurement checksum
and graph identity through the existing loader. The database retains original
node/segment/arc records, non-topology graph metadata, ordered per-segment
measurements, graph identity, measurement checksum, and terrain method. Records
currently use JSON payloads inside indexed tables; this is an initial import and
query boundary, not the final prepared-feature schema.

An R-tree indexes physical-segment envelopes in EPSG:3067. Ordinary indexes
support segment endpoints and arc segment/source lookups. `query_network` returns
complete candidate segments, their endpoint records, directed arcs, and optionally
their full measurements. Selection is conservative envelope overlap; callers
perform exact geometry checks in the application. No geometry is clipped or
reprojected in SQL, and source geometries/chainages are preserved.

Returned `frontier_nodes` identify loaded endpoints incident to excluded physical
segments. They are conservative topology markers, independent of request policy;
they do not establish that a legal continuation or better route exists. They
prevent treating an unloaded connection as a genuine network dead end. A complete
selected segment can extend outside the query box, so boundary handling cannot
rely only on endpoint coordinates or route-box intersection.

Export writes a staging database beside the destination, closes it, then publishes
without overwriting an existing file. Readers own a read-only connection with a
configurable page-cache target (16 MiB by default); this is not a cap on total
process or OS cache memory. A connection should stay with its owning thread and
be closed by its caller. Opening checks schema version without reading the network.
Offline import still parses the source JSON and packs its measurements in memory.

The running API does not consume this database yet. The
[migration plan](roadmap.md#next-sqlite-storage-and-bounded-network-loading)
defines prepared-feature persistence, selective delivery, bounded local search,
and planner area expansion. Existing packed topology/geometry/measurement data
remain useful for loaded areas; global dense indices must become local to each
working set while external dataset IDs remain stable. Globally computed display
and routing properties must be carried into local windows before API migration.

## Delivery and request lifetimes

Terrain tiles are 256-pixel Web Mercator PNGs, reprojected directly from derived
COGs. Contours use source elevation, ContourPy, and a separate MVT encoder.
Terrain/contour cache lookups share duplicate in-flight work while permitting
distinct tile renders; derived-core generation has a separate process-local lock.

Path MVTs use buffered clipping, zoom-dependent simplification and grade/distance
bins, while keeping segment IDs, eligibility, quality, and inclusion boundaries.
Inspection retains full measurements. Static tiles use immutable versioned URLs.
Display intervals share the measurement store's segment offsets, retain surface
classes once per segment, and store flat-run thresholds and presence flags in
read-only numeric buffers. `DisplayIntervals` reconstructs detached interval
records on access; no per-interval Python records remain retained. This layout
can be built for a loaded network window without persisting global dense indices.
Spatial cell rows retain immutable int64 interval-index
buffers; the building-access display view shares rows unchanged by filtering.
Candidate queries materialize sorted Python indices only for requested cells.
Tiles read packed columns directly through native
memoryviews; GeoJSON is assembled only for viewport responses. Static tiles evaluate policy eligibility
for candidate segments without constructing a whole-network routing index.
Routing and exploration prepare and cache policy eligibility when a search needs it.
Start/policy/budget queries prepare process-local snapshots containing immutable
bounded search results. Interval states, midpoint distances, and boundary flags
are calculated only for the spatial candidates of requested buffered tiles.
Navigation and tile variants reuse the same search; encoded tiles are cached.
Snapshot memory accounting charges the shared immutable distance buffers to each
entry conservatively; creating a snapshot does not copy search results. Snapshots and tiles have
byte-bounded TTL/LRU caches. Expired snapshots return 410; the client recreates
once. Use one API worker until snapshots can be shared or reconstructed.

Exploration runs only a distance-bounded Dijkstra search. Eligible intervals not
reached within the budget share the `beyond_budget` state and faint styling,
including disconnected intervals. No whole-network connectivity search runs;
policy-excluded intervals retain their separate state.

Run requests split both snapped endpoints in one temporary overlay. The planner
uses A* with a calibrated graph-distance lower bound, lazy shared shape integrals,
directed hill features, and fixed return-leg reuse context. Costs are finite,
nonnegative, and at least physical length. Legs are optimized sequentially;
there is no globally optimal loop guarantee. Request caches do not persist
across requests. See the [routing reference](reference/routing-mathematics.md).

Graph-index preparation batch-projects node and segment coordinates to EPSG:3067
and calibrates the distance lower bound once. Policy indexes share this geometry.
Endpoint overlays project only their new geometry and lower the calibration scale
if needed for their partial arcs. Shape integrals remain request-specific because
their fields depend on the selected endpoints and settings.

`routing.fields.integrate_fields` calls a scalar Rust kernel once per geometry,
passing coordinates and field settings and returning radial/right integrals and
projected length. The PyO3 adapter copies the small coordinate sequence into a
Rust vector. The pure kernel borrows its slice and has no Python dependencies or
callbacks. The previous Python implementation remains as `integrate_fields_python`
for comparison; there is no automatic fallback when the extension is missing.
The kernel preserves midpoint sampling and the fixed left/right frame. Python
checks the request deadline before and after the call; Rust receives a remaining
duration and checks its own elapsed time at bounded sample intervals. The first
binding keeps the interpreter lock during each short call. A*, cost composition,
and request caches remain in Python; spatial batching and native graph ownership
are [later decisions](roadmap.md#next-rust-integration-kernel-and-interactive-routing).

Each policy index prepares an arc eligibility mask and segment directions.
All policies share outgoing/incoming arc-index buffers and int64 node offsets;
searches skip disabled arcs without reparsing pedestrian tags. Request-specific
segment blocks still apply. Endpoint splits inherit their physical segment's
prepared directions. Applying a policy recomputes its mask from the full topology,
so relaxing a policy restores connections. Rebuild the policy index after changing
access or direction tags. Existing blocked-node and classification metadata remain
policy-local alongside the mask.

A* uses prepared integer node, arc, and segment indices and packed adjacency,
and per-search lists for distances and predecessors. Planner cost callbacks receive
prepared integer arcs, including their physical direction; heuristic callbacks
receive integer node IDs. They use aligned projected-coordinate references and
integer-keyed sparse request caches without looking up JSON records or string IDs.
External results retain string IDs. Heap entries retain the original string node
name as a tie key so equal-cost routes preserve their previous ordering. Integer
IDs are local to a prepared graph, not serialized identifiers. Policy changes
share the base integer topology and retain separate eligibility masks. Endpoint overlays
append a few indices and override only affected rows, sharing the full base arrays
without copying them per request. Rebuild the graph index after topology changes.

Arc source/target/segment indices use read-only int64 columns; length and retained
ascent/descent/terrain metadata use float64 columns. Direction and optional-field/
integer-spelling flags preserve external records and canonical graph identity.
No base arc dictionaries, persistent per-arc Python records, or string adjacency
lists remain. `ArcRecords`, `ArcLookup`, and `AdjacencyLookup` are on-demand views
for serialization and legacy consumers. Numeric search loops read buffers directly;
indexed cost callbacks receive transient immutable `IntegerArc` named tuples.
Endpoint overlays keep only their small extra records and row overrides. Canonical
graph hashing batches arc materialization to avoid recreating the full dictionary
tree. Full artifact export materializes views at its serialization boundary.
Hill storage shares that integer graph's segment ordering and ID mapping. It
retains read-only float64 arrays for directional segment totals, interval chainage,
and directional interval features, with int64 offsets identifying each segment's
interval range. The seven feature columns follow `HillFeatures` field order.
Whole-segment traversals read totals directly; partial traversals binary-search
the original graph chainage and scale the overlapping intervals. Projected length,
uncertainty, ascent/descent, and directional costs retain their existing meanings.
Small feature records are constructed for request costs and explanations; per-interval
Python hill records are temporary preparation data, not retained alongside arrays.
Requests reject a hill store with a different graph identity or segment ordering;
policy views and endpoint overlays share the base ordering.

Endpoint part references and return-leg reuse sets use integer segment indices;
parts retain the original physical index and graph chainage. Shape features remain
lazy per overlay segment and hill features per directed arc. Selected traversals
are translated to external physical IDs when producing explanations and responses.
Generic search retains record/string callback adapters for existing CLI and
comparison callers. Both policy views and endpoint overlays share the base
coordinate-reference tuples, with small appended tuples for temporary geometry.
The existing string geometry maps remain for preparation and display; the tuples
share their point objects rather than duplicating coordinate values.

Bounded exploration Dijkstra also traverses prepared integer adjacency. Its result
contains immutable native float64 values indexed by node, plus int64 indices in
discovery order. Unreached values are infinity. The shared base node-ID tuple binds
the result to the correct integer ordering; a small extra-ID tuple covers temporary
start nodes. Search uses mutable numeric working arrays and seals them into bytes
before publishing. Search caches and snapshots share the same sealed result,
including when a smaller budget reuses a larger search. Point/interval queries
translate prepared endpoints to integer indices; legacy callers of
`shortest_distances` receive a string-keyed dictionary through an explicit adapter.
Discovery order and external-ID heap tie ordering preserve previous results.

Distance buffer payload is `8 N + 8 R` bytes for N graph/overlay nodes and R reached
nodes. This has a per-node floor even for small budgets; it avoids dictionary/float
object overhead and snapshot copies when many nodes are reached. Snapshot cache
limits apply to the full payload. The [exploration report](archive/history/37-indexed-exploration.md)
records the latency/storage tradeoff and tile equivalence.

Graph coordinates are packed once at base-index construction. WGS84 segment
vertices and projected segment vertices use float64 pair buffers and shared int64
segment offsets; projected nodes use a separate float64 pair buffer. Prepared
node/segment ID tuples share the integer graph's ordering. No projected string maps
or retained per-point Python tuples remain. `PackedLine` is a small read-only
sequence view on a segment's WGS84 buffer, preserving existing geometry consumers
without retaining coordinate lists. Optional integer-coordinate masks preserve
published JSON number spelling and graph checksums. Inspection/export materialize
geometry at their serialization boundaries. Endpoint overlays retain sparse added
points while sharing all base buffers. Node metadata still retains its WGS84
longitude/latitude scalars; measurements retain their independently sampled vertices.

Raw node/segment metadata and object-based display indexes remain due for
the next [shared-index migration](roadmap.md#next-shared-integer-indexing-and-compact-prepared-data).
These storage/callback changes do not alter the cost model or integration kernel. The
[packed-hill report](archive/history/35-packed-hill-storage.md) records equivalence
and storage measurements; the [integer-cost report](archive/history/36-indexed-planner-costs.md)
records the following callback/cache migration. Persistence and startup
improvements remain separate.
The [measurement storage report](archive/history/38-packed-measurements.md)
records the retained-memory reduction and output equivalence for packed samples.
The [geometry storage report](archive/history/39-packed-geometry.md) records the
following WGS84/projected packing and standalone capital-area memory measurements.
The [arc storage report](archive/history/40-packed-arcs.md) records shared topology,
policy mask selection, output equivalence, and the memory/search-time tradeoff.

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

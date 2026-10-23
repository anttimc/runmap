# Architecture

Runmap is a local Python processing/API package with a Rust integration kernel
and a TypeScript/MapLibre web application. Numerical work also uses NumPy, SciPy,
and Rasterio/GDAL; the optional
web dependencies add FastAPI, Shapely, ContourPy, Pillow, and an MVT encoder.
Vite builds the frontend. SQLite catalogs elevation products and supports
selective path delivery. With a prepared network database, config, static tiles,
inspection, and snapping do not load the complete graph. Exploration and planning
load bounded local graphs without reading the source JSON.
JSON-only deployments retain the previous loader. There is no database server.

## Data flow

```text
MML elevation -> validated source COGs + immutable coverage manifest
             -> haloed smoothing and differentiation -> derived terrain COGs
             -> PNG terrain tiles / source-elevation contour MVTs

OSM extract -> directed pedestrian graph -> sampled gradient intervals
            -> graph.json + content-addressed measurements
            -> prepared network.sqlite -> static path MVTs, inspection, snapping
                                       -> local graphs -> forward-distance queries, run planning
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
| `paths/database.py`, `paths/database_features.py` | Offline SQLite export, prepared-feature persistence, and selective read-only queries |
| `paths/local_network.py` | Request-sized packed routing, measurements, hills, and unloaded/source-boundary markers |
| `paths/preparation.py`, `paths/coverage.py` | Dataset-wide feature preparation and shared source-coverage semantics |
| `paths/database_pool.py`, `api/sqlite_data.py`, `api/sqlite_paths.py` | Bounded connection leases, SQLite dataset lifetime, and selective API delivery |
| `api/sqlite_exploration.py` | Local exploration envelopes, frontier retries, request limits, and retained graph accounting |
| `api/sqlite_runs.py` | Local run-planning windows, candidate boundary checks, expansion attempts, and area-limited outcomes |
| `paths/exploration.py`, `routing/profiles.py` | Shared eligibility, snapping, temporary start overlay, and forward-distance inclusion |
| `paths/dead_ends.py`, `paths/flat.py` | Building-access visibility and flat-stretch display metadata |
| `routing/search.py`, `routing/run_planner.py` | Generic directed search and two-leg candidate orchestration |
| `routing/integer_graph.py` | Prepared dense node/segment/arc indices and shared sparse endpoint overlays |
| `routing/arc_storage.py`, `routing/serialization.py` | Packed arc columns, lazy external record views, and explicit JSON boundaries |
| `routing/node_storage.py`, `routing/segment_storage.py`, `routing/metadata_strings.py` | Packed node/segment metadata and pooled strings sharing IDs and geometry |
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
The JSON loader checks checksum and graph identity and builds spatial/display
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
sidecar into a schema version 2 SQLite database. Export verifies measurement checksum
and graph identity through the existing loader. The database retains original
node/segment/arc records, non-topology graph metadata, ordered per-segment
measurements, graph identity, measurement checksum, and terrain method. Source
records and variable-length feature data use JSON payloads inside indexed tables;
query keys, directions, flags, projected node points, and flat thresholds use
ordinary SQLite columns. Measurement rows are indexed by segment and interval
order, with no repeated per-segment measurement document.

Export prepares the complete source network before persisting features:

- Physical segments retain projected geometry, surface class, prepared boundary
  blocking, and full building-access classification evidence/attachment metadata.
- All four supported surface/stair policies retain forward/backward permissions
  and rejection reasons, including node restrictions and boundary blocking.
- Display intervals reference their original measurements and retain flat-run
  thresholds plus source-coverage-band flags. A separate EPSG:3067 R-tree indexes
  interval envelopes, allowing small display queries without reading every sample
  on a long segment. Building-access hiding filters candidates before decoding.
- Hill records retain directional segment totals and ordered interval features
  with original graph chainages, sufficient for full and partial traversals.
  Missing-sample segments retain the same conservative fallback features as the
  reference loader. Topology-only exports have no hill or display records.

Metadata includes source coverage, the globally calibrated heuristic distance
scale, terrain valid/trusted fractions, and a feature identity derived from graph
identity, measurement checksum, and model settings. Flatness thresholds/length,
classification policy/limit, pedestrian policy definitions, coverage/boundary
versions, and complete hill-model settings are versioned. Opening rejects schema
or model mismatches and inconsistent feature identity without scanning the network;
schema version 1 databases must be re-exported into a new destination.

An R-tree indexes physical-segment envelopes in EPSG:3067. Ordinary indexes
support segment endpoints and arc segment/source lookups. `query_network` returns
complete candidate segments, their endpoint records, directed arcs, and optionally
their full measurements. `query_segment_features`, `query_projected_nodes`, and
`query_display_intervals` retrieve prepared subsets independently. Selection uses
conservative envelope overlap; callers perform exact geometry checks in the
application. No geometry is clipped or
reprojected in SQL, and source geometries/chainages are preserved.

Returned `frontier_nodes` identify loaded endpoints incident to excluded physical
segments. They are conservative topology markers, independent of request policy;
they do not establish that a legal continuation or better route exists. They
prevent treating an unloaded connection as a genuine network dead end. A complete
selected segment can extend outside the query box, so boundary handling cannot
rely only on endpoint coordinates or route-box intersection.

`load_local_network` packs these selected records into the existing integer graph,
metadata, geometry, measurement, and hill containers. Each window owns dense local
node/segment/arc indices while preserving external IDs and dataset/feature identity.
Measurements and hill arrays share the local graph ordering and retain complete
segment chainages, including partial endpoint traversal support. Stored policy
permissions, rejection reasons, route-boundary blocking, building-access evidence,
and directional hill features are used directly; cropped topology is not
reclassified. Local coordinates are projected in the application, while heuristic
distance scale retains the dataset-wide lower bound. Hill valid/trusted fractions
describe the loaded subset; dataset fractions remain in metadata.

The window exposes both stable frontier IDs and their local node indices, plus
source-coverage-limited segment IDs evaluated against the original coverage.
Frontier markers remain independent of policy, physical dead ends, and source
coverage. An empty window has valid empty packed buffers; topology-only databases
omit measurements and hills. Raw query records and decoded prepared-feature
objects are temporary and released after packing. Exploration requests use
`include_terrain=False`, avoiding local sample/hill loading entirely; display and
inspection continue to query individual intervals separately. The
[local-network report](archive/history/46-sqlite-local-network.md) records fixture
parity and fresh-process area/memory measurements.

Export writes a staging database beside the destination, closes it, then publishes
without overwriting an existing file. Readers own a read-only connection with a
configurable page-cache target (16 MiB by default); this is not a cap on total
process or OS cache memory. Standalone connections stay with their owning thread
unless explicitly opened for exclusive cross-thread leases. Offline import still parses the source JSON and prepares
its complete routing, measurement, classification, and hill data in memory.

The API selects `RUNMAP_PATH_DATABASE` when set, otherwise a `network.sqlite`
beside `RUNMAP_PATH_GRAPH` when present. Without either it uses the JSON loader.
A selected missing, corrupt, stale, or incompatible database reports unavailable
or failed; it does not silently fall back to the global JSON loader.

SQLite startup reads only versioned metadata, source coverage, provenance, initial
view, and precomputed hidden-segment/interval counts. These presentation values
are additive schema-2 metadata: earlier exports without them must be regenerated
for API use. Startup performs no network-table count, source JSON read, spatial
index rebuild, classification, or hill preparation.

`DatabasePool` opens two read-only connections by default, each with a 16 MiB
page-cache target. A bounded queue leases each connection exclusively to one
request, including across API threads. Duplicate-generation checks protect pool
creation. Exhausted leases time out after five seconds with HTTP 503. Shutdown
closes idle connections and closes active ones when returned. No decoded network
window is cached for display/inspection/snapping; encoded tiles retain their
existing byte-bounded TTL/LRU cache.

Viewport and static tile queries use projected interval envelopes plus persisted
policy data; application geometry checks, styling, coalescing, and MVT encoding
retain their existing semantics. Viewport iteration stops at the feature limit.
Tiles first collect ordered interval IDs in a packed integer buffer, then fetch
at most 512 records per connection lease. Styling, interval-state calculation,
coalescing, and MVT encoding run outside those leases, allowing concurrent tiles
to share the bounded pool. Tiles use the saved coverage flags. Large low-zoom
tiles can still take seconds to encode; batching prevents that CPU work from
monopolizing database connections.
Inspection reads one segment and its ordered samples by ID. Snapping queries a
projected radius box and refines distances in Python, retaining hidden building
access as legal snap candidates. Inspection/snap calculations are shared with the
reference loader. Tile versions additionally include the prepared feature identity.

SQLite exploration, snapshots, and inspection with start-distance information
prepare a local graph around the full snapped segment's EPSG:3067 envelope,
expanded by `budget_m / distance_scale + 1 m` in every direction. The complete
start geometry protects partial starts even on long/bent segments. The persisted
global distance scale bounds subsequent arc endpoint displacement; selecting
complete incident segments preserves continuations without relying on the
viewport or on a clipped geometry's apparent dead ends.

The request runs forward distance search and checks whether any unloaded frontier
node was reached within budget. If so, it discards that working set, doubles both
box dimensions, and repeats under one deadline. Defaults allow two retries,
2,500 km² query area, and 50,000 selected segments. Segment count is checked with
a bounded spatial count before decoding network records. Reaching configured
limits returns HTTP 422 with explicit `area_limited` status, reason, attempted
bounds, and retry count; it does not report a genuine dead end or lack of source
coverage. Preparation timeout returns HTTP 504. Exclusive connection leases bound
concurrent local preparation; lease waiting, SQLite progress callbacks, packing
checks, and Dijkstra share the preparation deadline.

With a matching numeric artifact, exploration uses the same immutable chunk
buffers, native graph index, and per-artifact LRU cache as run planning. Native
forward Dijkstra returns sparse reached-node IDs and distances, independent of
native graph ordering and buffer lifetime. Snapshots retain these sealed result
bytes and search metadata; they do not retain graph chunks, per-edge Python
records, or native graph owners. Recursive storage accounting charges distance
buffer owners once per snapshot. Closing or evicting the graph cache leaves
existing snapshots usable. Byte/TTL snapshot eviction releases result ownership.
The reference backend retains its local graph and sealed distance buffers together.

Identical pending preparations coalesce before graph loading, and waiting callers
retain their own deadlines. Snapshot keys include prepared feature identity,
algorithm version, policy, snapped segment/offset, and budget. Static and snapshot
tiles query display intervals from SQLite. Numeric snapshot queries also select
persisted numeric endpoint identities and physical segment length, resolving
point distances and whole-interval inclusion using sparse distance lookups plus
the virtual start anchor. No graph or measurement sidecar is reconstructed.
Source-coverage flags, policy exclusions, styling, inspection direction, and tile
LOD retain reference semantics. Direct exploration and start-distance inspection
share the same snapshot cache. These operations need no source JSON.

SQLite run planning snaps both endpoints using indexed interval queries and
starts with an EPSG:3067 square centred at their midpoint, enclosing the circle
whose diameter is their straight-line separation enlarged by 50%. The square's
side is floored to 1,000 m by default. The viewport does not
determine legality. Each attempt loads complete local topology and persisted
directional hill features in the same dense ordering, without loading raw
measurement arrays. Preference capabilities and configuration retain dataset-wide
coverage fractions; actual traversal observations describe the selected route.

Shortest-path search reports popped frontier nodes, including the destination.
When a matching `numeric-routing-v2.sqlite` artifact is available, run planning
uses Rust traversal over immutable numeric chunk buffers. Startup reads metadata
only. A per-artifact LRU retains working sets within a configurable accounting
budget; searches own their overlays, costs, and mutable state. Expansion constructs
a new numeric working set; chunks are not yet shared between different bounds.
The renderer reads only selected physical segment records and produces the same
API response schema. Equal-cost ties use numeric identities and can select a
different equivalent route. Without a numeric artifact the reference SQLite
planner remains available. Distance exploration uses the same numeric graph service.
Numeric exports omit segments forbidden by every supported profile, rebuilding
node incidence and chunk envelopes over the retained policy union; see
[numeric preprocessing](numeric-routing-format.md#export-and-read).

Run planning retries when a candidate search reaches an unloaded connection,
when any candidate geometry enters the query-boundary band, or when an incomplete
candidate assignment leaves unloaded connections in the window. The boundary
band is the larger of 100 m and 5% of the shorter box dimension. Both dimensions
double on retry. Each retry discards prior legs and reruns all side assignments
against the same expanded local graph. This is a heuristic: a better route
outside the window can remain undetected; global optimality is not promised.

Defaults allow five retries, 2,500 km² query area, and 650,000 selected segments.
Area and bounded segment-count checks precede decoding. Counts read only the
source segment R-tree, stopping at the cap plus one; source export maintains one
index entry per segment. Policy filtering happens after admission, so these
counts include ineligible source candidates. The
[admission-count report](archive/history/50-segment-count.md) records equivalence
and complete-request timings. Run search metadata
records each attempted box, node/segment counts, retry reason, reached-frontier
and query-boundary involvement, and source-coverage proximity separately.
Exhaustion returns `status=area_limited`, an explicit reason, and no candidate
legs from discarded windows. A graph with no relevant unloaded connections may
return `unreachable`; source coverage limitations remain a separate flag/note.
The UI distinguishes area limits from unreachable endpoints.

The existing two run slots bound concurrent preparation/search. One deadline
covers endpoint snapping, connection waits, SQL, local packing, every retry,
cost preparation, and search. Completed candidates from a deadline-interrupted
attempt can still be serialized and returned with `status=deadline`. Busy planner
slots return HTTP 429; busy connection leases return HTTP 503. Local graph and
hill stores are request-owned and released after response construction, with no
retained planner graph cache.

The lazy full-JSON bridge has been removed. With SQLite selected, all path API
operations work without the source graph/measurement files, and config advertises
planning from database metadata. Dense indices belong to each working set while
external dataset IDs remain stable. Persisted global classifications and source
boundaries are used directly rather than recomputed on those windows.
The [SQLite feature report](archive/history/43-sqlite-features.md) records fixture
parity checks and an initial real-extract export/query probe; cold-cache and
full-application benchmarks remain open.
The [SQLite API report](archive/history/45-sqlite-api.md) compares selective delivery
with the JSON reference in separate reader processes, excluding offline export.
The [SQLite exploration report](archive/history/47-sqlite-exploration.md) records
local-search parity, request limits, snapshot ownership, and reader measurements.
The [SQLite planner report](archive/history/48-sqlite-runs.md) compares six saved
route requests, including hill preferences, and records expansion/memory costs.

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
entry conservatively; reference SQLite entries also charge their owned local graph,
while numeric SQLite snapshots charge only their independent sparse distances.
Creating a snapshot does not copy search results. Snapshots and tiles have
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
so relaxing a policy restores connections. Prepared metadata lookups return
detached dictionaries; editing one does not change the base. Make an editable
document copy and rebuild after changing source access or direction tags.
Existing blocked-node and classification metadata remain
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
graph hashing batches node, segment, and arc materialization to avoid recreating the full dictionary
tree. Full artifact export materializes views at its serialization boundary.

Node WGS84 coordinates use float64 values with integer-spelling flags. Sparse
optional node strings and ordered segment tags use pooled strings with int64 row
offsets and uint32 key/value codes. Segment endpoints share integer node IDs;
numeric metrics use float64 columns, sample counts use int64, and separate
presence, boolean, and integer-spelling flags preserve optional record semantics.
Way IDs are pooled without changing their external strings. `NodeRecords`,
`SegmentRecords`, and `MetadataLookup` reconstruct detached public records and
reuse the integer graph's ID tuples/maps. Segment geometry shares the existing
packed coordinate store; no retained record dictionaries or per-segment geometry
views remain. Policies share these views, while endpoint overlays retain only
their sparse additions. Deep-copying a prepared document materializes editable
metadata records while keeping read-only geometry shared. These containers can
also represent a locally loaded network window; they require no SQLite schema
change or persistence sidecar. The [metadata report](archive/history/43-packed-metadata.md)
records memory and equivalence measurements.

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
comparison callers. Both policy views and endpoint overlays share the packed base
coordinates, retaining only small appended tuples for temporary geometry.

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

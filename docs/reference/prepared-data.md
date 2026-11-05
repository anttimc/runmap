# Prepared data contracts

This reference covers artifact identity, publication, and spatial selection.
See the [architecture](../architecture.md) for component relationships,
[development](../development.md) for preparation commands and configuration, and
the [numeric format](../numeric-routing-format.md) for the native binary layout.
Module docstrings describe the code responsible for each artifact.

## Environment raster bundles

Environment fields are independent of graph preparation and are not joined to
routing artifacts. The first baseline uses a 10 m EPSG:3067 grid aligned to
metric multiples of ten. Preparation reads the acquisition halo before cropping
the core. A completed `manifest.json` is the publication marker; existing output
directories are never overwritten.

`fields.tif` has Float32 bands named by description, with `-9999` nodata:

| Band | Meaning |
| --- | --- |
| `woodland_coverage` | Union fraction of explicit woodland, estimated from 2 m cell-center samples; holes retained |
| `forest` | Woodland fraction averaged over a disk of 100 m by default |
| `greenery` | V3 maximum of `forest`, weighted park coverage and weighted reserve coverage; replaces the displayed Forest surroundings score |
| `reserve_coverage` | V3 union fraction of `leisure=nature_reserve` polygons, sampled at 2 m |
| `park` | Union fraction of `leisure=park` polygons using 2 m center samples; holes retained, nature reserves excluded |
| `wetland` | Union wetland fraction sampled at 2 m, independent of woodland |
| `forest_evidence_gap` | Mean designation fraction lacking explicit woodland; retained diagnostic, not a v3 review-hatching input |
| `water_interior` | Fraction inside all mapped water polygons in v3; tagged lakes only in v1/v2 |
| `lake_distance_m`, `sea_distance_m` | Distance to occupied shoreline cells; absent shoreline support is nodata |
| `water_polygon_distance_m` | V3 distance to occupied boundary cells of any mapped water polygon |
| `water` | Linear 100 m proximity to any water-polygon boundary or coastline in v3; line waterways excluded |
| `water_evidence_gap` | Zero in v3 because all water polygons are accepted; v1/v2 retain other-water review proximity |
| `review_candidates` | V3 limited-road-context mask, used directly for candidate hatching |
| `traffic` | Normalized dominant-road noise discomfort in v2; v1 retains the historical linear influence |
| `traffic_noise_db` | V2 dominant source intensity expressed as modeled dB relative to the configured intensity reference; nodata if no retained source contributes |
| `traffic_context_support` | V2 flag: an exterior source cannot change the dominant-source score under configured source-strength limits, or its bound is below the display floor; not evidence of real-world completeness |
| `traffic_outside_bound_db` | V2 upper bound for an unknown source beyond the retained rectangle, within the model assumptions |
| `context_support` | Configured finite processing radius fits within the retained extent; not full acoustic support or real-world completeness |

Lengths/radii/weights are recorded in the configuration. Shorelines and roads
use all-touched 10 m cell occupancy plus Euclidean distance transforms. This
approximates geometry distances by roughly a cell diagonal. Empty geometry sets
produce zero mapped-context scores; absent distance channels become nodata.
Mapping completeness remains unverified everywhere, independently of the support
band. V3 accepts park/reserve designations and all water polygons as inputs;
absence of a candidate flag does not establish complete evidence. Invalid source
geometry is skipped and counted in the manifest.
Distances beyond configured support refer only to retained source objects;
they do not establish the nearest real-world shoreline outside that support.

### Greenery and water, version 3

The displayed greenery score is `G = max(F, w_p P, w_r R)`. F is the retained
woodland surroundings mean within 100 m; P and R are park and nature-reserve
union fractions in the cell. Woodland has fixed weight 1; park and reserve
weights default to 0.5 and must remain below 1. P/R have no neighborhood blur,
so designation contributions occur only through their polygon footprints.
Overlaps take a maximum rather than summing. This intentionally broadens the
target to green areas without asserting that every designation contains trees.
Park coverage remains a diagnostic band rather than a separate user-facing class.

Water uses all selected polygonal water evidence: tagged lakes, other `water=*`
or `natural=water` areas, reservoir/basin land use, and polygonal `waterway=*`
features. It retains polygon holes and calculates distance to their boundaries,
then combines that distance with coastline distance for the 100 m proximity
curve. Line waterways remain excluded. Water interiors are separately identified;
deep water can be farther than 100 m from a shore. Non-lake bodies no longer
produce review-candidate hatching. Greenery, water, wetlands and road context
remain independent fields.

### Road-noise proxy, version 2

For road class c, reference intensity `I_car`, class factor `w_c`, reference
distance `r₀`, and spreading exponent `p_c`:

\[
I_c(r)=I_{car}\,w_c\left(\frac{r_0}{\max(r,r_0)}\right)^{p_c},\quad
L_c(r)=10\log_{10}\frac{I_c(r)}{I_0},\quad
T=\operatorname{clip}\left(\frac{\max_c L_c-L_{floor}}{L_{full}-L_{floor}},0,1\right).
\]

Ideal line-source **intensity** falls as `1/r`; point-source intensity falls as
`1/r²`. These correspond to approximately 3 dB and 6 dB loss per distance
doubling. Pressure amplitude has different powers; the intensity ratio uses
`10 log₁₀`, rather than the pressure-ratio `20 log₁₀`.
[FHWA's measurement handbook](https://www.fhwa.dot.gov/Environment/noise/measurement/handbook.cfm)
describes these ideal spreading relationships.

Defaults: `r₀=10 m`, `I_car=10⁻⁵ W/m²`, `I₀=10⁻¹² W/m²`, hence a nominal car
reference level of 70 dB; floor 45 dB and full scale 75 dB. These are explicit
experimental assumptions, not measured car emissions or validated annoyance
thresholds. Current class factors are motorway/trunk 1, primary 0.8, secondary
0.6, tertiary 0.1, and residential/living-street/unclassified/service 0.
Zero-weight classes contribute no intensity. These factors scale **intensity**,
not dB directly.
Motorway/trunk now use the effective exponent `p=1.5`; other classes use `p=2`,
representing a sparse passing-car envelope at the nearest road point. Traffic steadiness, rather than class alone,
determines the appropriate source type in reality. The configurable class mapping
is a proxy assumption.

The intermediate exponent gives about 4.5 dB loss per distance doubling, between
the ideal 3 dB line and 6 dB point laws. It is equivalent to adding
`5 log₁₀(r/r₀)` dB of attenuation to the ideal line model. This is a uniform
effective approximation; no actual ground cover or barrier geometry is used.
[FHWA's noise guidance](https://www.fhwa.dot.gov/environMent/noise/regulations_and_guidance/polguide/polguide02.cfm)
describes an approximately 4.5 dB-per-doubling rule for soft-ground highway
propagation; applying it throughout this pilot remains a calibration assumption.

Clamp distance at the reference radius to avoid a near-source singularity. Take
the nearest source per class, then the maximum intensity across classes; do not
sum OSM way pieces or multiply energy when one road is split into more objects.
This is a dominant-source approximation, not the acoustic sum from independent
vehicles, an hourly equivalent level, or a calibrated dBA prediction.

There is no hard 300 m acoustic cutoff. Roads retained in the extract can
contribute farther away. For outside-context support, bound an unknown source
at the nearest edge of the retained rectangle using the strongest configured
reference source. Allow a cell-diagonal distance margin for rasterization when
comparing known and exterior intensities. Flag unsupported cells when an exterior
source could dominate above the floor. Candidate hatching includes those flags;
the numeric estimate still describes known sources and can underestimate total
exposure. Mapping completeness remains independently unverified everywhere.

`evidence.json` contains deduplicated source IDs, tags, independent memberships,
and projected EPSG:3067 geometry. It is a local analytical document, not standard
WGS84 GeoJSON. Nearby source inspection uses exact geometry distances at the
click, so its road influences may differ from the cell approximation. It returns
at most five objects per evidence group, plus full counts within configured
support. V2 road evidence extends to each class's display-floor distance and is
ranked by modeled discomfort; exact-geometry dB and source type are included.
Park and wetland objects within 15 m are shown to explain partial-cell coverage.
Park and nature-reserve memberships are distinct in newly prepared evidence;
both remain candidates for reviewing missing woodland. Designation alone never
raises woodland coverage. In v3, their weaker designation contributions raise
greenery only within their polygons. Parks can overlap woodland without losing membership.

Four user-facing score PNGs, a park diagnostic preview, and candidate hatching are warped offline to
Web Mercator. The manifest stores exact image-corner WGS84 coordinates, band
order, source/configuration and implementation/dependency identities, artifact
checksums, source dates, and preparation time. Dataset identity binds source
export and full acquisition-manifest identity (including core and halo),
configuration, implementation contents, and dependency versions.
The API rejects stale identities and source/raster mismatches. Previews use
8-bit alpha for display; the TIFF retains numeric scores. No calibrated model,
measured sound level, complete ocean mask, or routing preference is implied.
The preview manifest lists available layers. The frontend omits standalone park
controls even for bundles supplying a diagnostic preview, and displays only score
channels actually present in the sampled TIFF.

See [usage](../guide.md#experimental-environment-rasters) and
[preparation](../development.md#prepare-environment-rasters).

## Elevation products

Named regions use EPSG:3067 and the native 2 m elevation grid. Source tiles are
5 km squares. Processing reads a 512 m halo around each analysis core, then
retains only that core. The configured region controls coverage; the exact
region extent and path-sampling settings are in configuration files rather than
implied by a place name.

The elevation catalog records acquisition batches, validated source tiles,
coverage manifests, region activation, and derived products. A manifest pins
source tile versions and checksums. Changing a region definition requires a
matching prepared manifest. Source rasters and provenance remain separate from
derived products so they can be reprocessed.

Derived Cloud Optimized GeoTIFFs contain unsigned 16-bit gradient magnitude and
uphill bearing. Their scales are 0.0001 rise/run and 0.01 degree; 65535 is nodata.
Derived identity includes source checksums, algorithm version, smoothing settings,
and halo size. Contours use source elevation. Path measurements sample the
derived gradient product; they do not contain absolute path elevation. See
[terrain conventions](terrain-mathematics.md#gradient-and-path-conventions).

Each newly prepared gradient COG has a sibling `<tile>.height.tif`, containing
one float32 band of smoothed ground elevation in metres, with NaN nodata on the
same native grid. It is the cropped smoothed surface used to calculate the
gradients, with the same source digest, algorithm version, wavelength, and halo.
Route profiles sample these height COGs using the path dataset's manifest and
wavelength, independently of the active background terrain preset. Existing
gradient tiles remain readable; rerunning region precompute adds missing height
siblings without rebuilding path or numeric routing datasets.

The store reuses matching ready products. Missing derivatives may be generated
on request; there is no automatic disk eviction or inter-process acquisition
lock. Preparation and serving commands are in
[Prepare elevation](../development.md#prepare-elevation).

## Published graph and measurements

The graph preserves OSM shared-node connectivity. Geometric crossings alone do
not create junctions. Physical segments own geometry and chainage; directed arcs
reference segments. Pedestrian permissions determine legal directions independently
of ordinary vehicle one-way rules. Import rules and later policies have distinct
roles: a runtime policy cannot restore geometry excluded during import.

Terrain preparation produces ordered per-segment intervals containing sampled
grade, height change, geometry, validity, and quality flags. Intervals retain both
projected chainage and graph chainage. Graph length uses geodesic geometry;
terrain integration uses projected length, so partial traversals must map between
the two chainages. Sampling density and smoothing come from the path configuration.

`graph.json` references a content-addressed `measurements-<sha256>.json` sidecar,
including checksum and size. Publication writes the graph last, leaving earlier
measurement generations readable. Loading verifies the sidecar checksum and its
graph identity. The explicit JSON backend loads the full graph and measurements;
it is also the input to SQLite export.

## Prepared network SQLite database

The [sqleton schema visualization](network-schema.svg) is generated from the
local database, including indexes, graph chunk tables, and SQLite R-tree shadow
tables. See [generation instructions](schema-visualization.md) to refresh it.

The current network schema version is **3**. Export prepares dataset-wide access,
boundary, display, classification, and hill features before persisting them.
Runtime windows use those results directly; cropping a graph must not change its
classification or the meaning of source coverage.

| Stored data | Contract |
| --- | --- |
| Nodes, segments, and directed arcs | Preserve external records and original geometry |
| Ordered measurements | Preserve both chainages, sample nullability, and quality flags |
| Segment policies | Persist directions and rejection reasons for all supported surface/stair profiles |
| Segment features | Persist projected geometry, surface class, route-boundary blocking, and building-access evidence |
| Display intervals | Retain flat-stretch thresholds and source-coverage flags, referencing original samples |
| Hill features | Retain directional totals and intervals sufficient for partial traversals |
| Dataset metadata | Identify the graph, measurements, prepared models, source coverage, heuristic scale, and presentation data |

Variable-length records and features use JSON payloads alongside indexed SQLite
columns. Separate EPSG:3067 R-trees index segment and measurement-interval
envelopes. SQL selects conservative spatial candidates; application code refines
geometry, snaps endpoints, and clips display output. The database does not require
SpatiaLite and does not clip routing geometry in SQL.

Opening checks schema/model compatibility and prepared feature identity without
scanning the network. Identity includes the graph, measurement checksum, and
versioned preparation models. Topology-only exports can omit terrain features;
the interactive path service requires measurements and coverage.

Export writes a staging database and publishes without overwriting an existing
destination. Schema/model changes require re-export to a new file. See
[SQLite export](../development.md#export-a-sqlite-network-dataset).

## Selected windows and identities

Spatial queries return complete selected segments, their endpoint nodes, and
directed arcs. Segments may extend outside the requested bounds. Routing must
preserve their original geometry and chainages, including at partial endpoints.

Unloaded connections are marked as frontiers. Frontier evidence is topological
and independent of the active policy; it does not prove a legal continuation.
Source-coverage proximity and genuine network dead ends are separate concepts.
Search expansion and failure reporting are described in the
[API reference](api.md#bounded-search-areas).

Packed Python graph containers serve offline preparation and the reference
backend. Their node, segment, arc, and interval indices are separate domains,
local to a prepared graph/window. Geometry, policies, measurements, and hill
stores must agree on that ordering. External IDs remain available for provenance,
inspection, and API output. Request endpoint overlays extend the base with sparse
temporary data; they do not renumber or mutate it. Internal array layouts and
record views are documented with their modules in `routing/` and `paths/`.

The heuristic distance scale is calibrated over the source dataset. A selected
window retains that lower bound; omitting a constraining arc must not increase it.
Hill coverage fractions in metadata describe the dataset, while selected-route
observations describe the actual traversals.

## Numeric routing artifact

`numeric-routing-v2.sqlite` is exported from the prepared network database. Its
metadata must match that database exactly. Network schema, numeric database schema,
and binary chunk versions are separate contracts. An explicitly selected missing
or incompatible numeric artifact fails loading; it is not silently replaced with
another dataset.

Numeric preparation assigns stable dataset-local identities and partitions
topology into spatial chunks. It preserves complete segments, shared junction
identities, policy permissions, and prepared costs. Segments forbidden by every
supported profile are omitted from the numeric policy union. Full-segment hill
totals are shared with graph buffers; partial endpoint interval detail is stored
separately and loaded only when needed.

The [numeric format](../numeric-routing-format.md) owns byte layout, versions,
validation, endpoint interval storage, and native buffer ownership. Historical
packing/export measurements remain in the [archive](../archive/README.md); they
are evidence for those dated implementations rather than current performance
guarantees.

## Regional environment bundles

`environment/regional.py` publishes schema 2 bundles with method
`mapped-context-regional-v3`. They contain `fields.tif` and a completion
`manifest.json`, without whole-region PNG previews or an in-memory evidence
export. The TIFF uses the same aligned 10 m EPSG:3067 grid, 2 m polygon coverage
samples, and baseline v3 band names as the pilot. No network identity or routing
join is implied. Default coverage is the existing 40 × 40 km capital-area square.

Offline processing uses 4 km cores and a spatial index over retained full source
geometries. Each core receives a halo covering all field radii and the greatest
configured road display-floor distance, plus three analysis cells for raster
uncertainty. Acquisition must supply at least that halo beyond the entire region.
Greenery, water, traffic and wetland scores agree across cores under the finite
display influences; no per-block normalization is used. Source geometry and index
memory scale with extract size; numerical coverage work scales with block size.

Regional diagnostic water distances beyond `water_radius_m` and road dB below
the discomfort floor use nodata. This bounds processing without implying precise
far-distance measurements. A missing dB diagnostic does not make the traffic
score unknown: retained sources below the floor give a valid zero display score.
`traffic_outside_bound_db` describes the processing block's retained context,
rather than an unlimited acoustic estimate. Mapping completeness remains
unverified even where context support is sufficient. Nodata is -9999 in the TIFF.
Nearest-neighbor internal overviews at factors 2–64 reduce zoomed-out display
reads; analytical sampling uses the full-resolution bands.

The manifest records source feature/acquisition identities and dates, configuration,
implementation/dependency identities, grid, bands, processing core/halo, category
counts, skipped geometries, output checksum, and elapsed preparation time. It is
written last; output directories cannot be overwritten. Failed partial outputs
have no completion manifest and cannot be served. This regional bundle is
independent of the source-inspection pilot.

## Shared OSM snapshot and regional source bundles

`sources/geofabrik.py` publishes an unfiltered Finland `finland.osm.pbf` plus a
manifest binding its resolved dated provider URL, provider MD5, SHA-256, byte
count, retrieval time, and licence. `download.json` pins the URL/checksum for
interrupted transfers; `.part` files are not published snapshots. Provider
metadata privacy omissions remain those of the downloaded Geofabrik extract.

`sources/pbf.py` imports both environment and highway evidence from that same
source. Its completion manifest binds source identity, PBF header timestamp and
coverage envelope, configuration, implementation/dependencies, output checksums,
geometry diagnostics, and processing time. The requested core plus halo must fit
inside the PBF header envelope. This check establishes bounding-box containment,
not completeness of all relations or real-world mapping.

Geometry is assembled before spatial selection. Environment objects intersect
the metric core plus halo and retain complete geometry; highway ways intersect
the metric core and retain complete references. The second node pass preserves
all referenced coordinates and node tags, including outside-core vertices. The
highway export is deliberately broader than runnable eligibility; the existing
graph preparation applies its access/direction/surface rules. Multipolygon
relations preserve holes; non-area relation semantics remain excluded. Missing
way locations and invalid geometry are counted; tagged unassembled area-relation
counts refer to the entire source. They do not certify local support.

`environment/manifest.json` uses the existing regional acquisition contract, so
raster preparation can consume either PBF-derived or Overpass-derived sources.
`network.json` uses the existing elements/node/way input format. Its `.json`
sidecar binds the source PBF and export checksum; explicit path preparation
verifies it and propagates source identities into graph provenance. Existing
Overpass JSON inputs without PBF sidecars retain their explicit-input behavior.
See [commands](../development.md#shared-finland-osm-snapshot-pipeline).

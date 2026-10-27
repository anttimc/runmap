# Prepared data contracts

This reference covers artifact identity, publication, and spatial selection.
See the [architecture](../architecture.md) for component relationships,
[development](../development.md) for preparation commands and configuration, and
the [numeric format](../numeric-routing-format.md) for the native binary layout.
Module docstrings describe the code responsible for each artifact.

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

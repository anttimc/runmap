# Shared Finland OSM snapshot — 2026-11-05

The regional Overpass retry saved no source responses after about five minutes.
The import switched to Geofabrik's dated Finland extract. General contracts and
commands are in [prepared data](../../reference/prepared-data.md#shared-osm-snapshot-and-regional-source-bundles)
and [development](../../development.md#shared-finland-osm-snapshot-pipeline).

The downloaded `finland-261103.osm.pbf` was 774,833,699 bytes, matched provider
MD5 `246cf127b3be08a4a2949ff3f9dfb585`, and has SHA-256
`801b38b66adfe267709f7da14004ae06abaf4f41dffa39c653aa1df00aa841c0`.
Its PBF header timestamp is 2026-11-03 21:24:23 UTC. The full source is retained
under `data/osm/raw/finland-2026-11-05/`, outside Git. Licence: ODbL 1.0.

Pyosmium 4.3.2 assembled full ways/multipolygons before selection into the
40 × 40 km EPSG:3067 core and 1000 m environment halo. The import selected
186,796 environment features and 344,674 highway ways, then loaded their
referenced nodes. It took 211.1 seconds. Eight tagged multipolygon relations
were unassembled **in the full Finland source**; this is not a local completeness
claim. The regional environment GeoJSON is 227,648,364 bytes (SHA-256
`8f33b758ee12a778572925c481b281bce9767f4766c42ca573a94a5a502ad421`);
the highway/node JSON is 325,826,093 bytes (SHA-256
`e9b4b34cdf7c4e25965c30b62e1b2c76338875f53b64f15e44fb8a0e8c6a7910`).
Both are retained under `data/osm/extracted/helsinki-capital-area-2026-11-05/`.

The baseline v3 regional raster is 265,413,720 bytes (SHA-256
`b9d69d0dbbf886de6335887976a4977a54023b339d21567ca08877023e0a42e2`).
Preparation took 69.03 seconds and reached 1.89 GB resident memory on this
macOS ARM machine. All five versioned tile layers returned PNG 200 responses
from the full regional TIFF at EPSG:3067 (380000, 6680000). The dataset ID is
`de078fed501c0d3b0e9290879479063183a61b848183e5c075ea06cbe5f27cbb`.
The TIFF is under `data/environment/prepared/helsinki-capital-area-v3/`.

The separate terrain-enriched graph contains 513,207 nodes, 651,605 segments,
and 1,303,210 directed arcs, with 99.38% terrain measurement coverage. Graph
preparation took 176.94 seconds and reached 7.35 GB resident memory. The graph
and measurements are under
`data/paths/geofabrik-2026-11-05/prepared/helsinki-capital-area/`.

SQLite network export took 309.82 seconds, reached 4.50 GB resident memory,
and produced 5,272,236,032 bytes. Numeric export took 72.28 seconds, reached
173 MB resident memory, and produced 635,858,944 bytes. Both databases passed
SQLite `PRAGMA quick_check`. These are separate products in the same Geofabrik
data root; the pre-existing served routing database was not replaced.

These products establish reproducible mapped source preparation. The eight
unassembled relations need regional relevance assessment before treating
specific candidate areas as complete. Real-data browser seams, overlap,
accessibility, and cold/warm interaction latency remain in the
[roadmap](../../roadmap.md#planned-environment-preferences-experiment).

Later on 2026-11-05, the local runner accepted the full-area map-layer visuals,
ordinary UI use, and rendering latency. That acceptance is qualitative; the
pilot timing observations remain the only recorded tile latency numbers.
Keyboard-only and screen-reader review remains deferred in the roadmap.

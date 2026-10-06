# 13. Runnable-path graph prototype

## Scope

The first path graph supports the flat 10 km loop experiment defined in the
[route-planning design](12-route-planning-design.md). Its preparation rectangle
is `E 373000–380000`, `N 6673000–6682000` in EPSG:3067. This covers the
Leppävaara/Kilo test neighborhood with context around its edges. Ring I remains
a route-search boundary to implement later rather than an OSM extraction cut.

Ruusutorpan koulu is identified by its public address, Leppävaarankatu 24, and
an approximate landmark coordinate. Preparation snaps that coordinate to the
nearest graph vertex and records both the vertex and distance. The first real
extract snaps 28.3 m to a degree-one paved service branch beside the school;
this is retained as a visible access stem.

## Preparation

```sh
PYTHONPATH=src .venv/bin/python -m runmap.cli paths prepare \
  leppavaara-flat-10k --dry-run
PYTHONPATH=src .venv/bin/python -m runmap.cli paths prepare \
  leppavaara-flat-10k
```

The bounded Overpass query downloads only the initial runnable highway classes.
The raw response and provenance sidecar are stored under `data/paths/raw/`.
Prepared outputs under `data/paths/prepared/` are:

- `graph.json`: vertices, physical segments, directed arcs, source identity,
  start snap, terrain method, and summary;
- `segments.geojson`: physical geometry and attributes for map inspection;
- `inspection.json`: compact graph and start-snap summary.

These local data products are ignored by Git. `--input` can rebuild from an
existing Overpass JSON response without network access, and `--without-terrain`
can inspect topology before derived terrain tiles exist.

## Initial path policy

The importer accepts footways, paths, pedestrian and living streets,
pedestrian-permitted cycleways, residential and service streets, tracks,
steps, and unclassified roads. It rejects area geometry, indoor paths,
construction, explicit pedestrian denial, and private/no access unless an
explicit pedestrian permission overrides the general access tag. A cycleway
also requires an explicit pedestrian permission; missing permission is not
treated as permission.

Ordinary vehicle `oneway` does not restrict runners. Only explicit pedestrian
direction tags create a one-direction arc. Bridges and tunnels connect only
through shared OSM node identifiers; geometric crossings do not create false
junctions. Tagged barriers become graph vertices with their access metadata
preserved for later enforcement.

Each physical segment owns its geometry and OSM-derived attributes. Directed
arcs reference the segment and carry direction-dependent length, ascent, and
descent. This avoids duplicating geometry while keeping travel direction
explicit.

## Terrain enrichment

The graph uses the existing 40 m spectral thin-plate terrain product. Segment
geometry is sampled every 10 m in EPSG:3067. At each interval, the prepared
terrain-gradient vector is projected onto the travel direction and integrated.
The reverse arc swaps ascent and descent. Coverage validity is recorded instead
of treating missing terrain as flat.

This measures change along the smoothed two-dimensional terrain surface. It is
appropriate for the first comparison, but bridges, tunnels, embankments, and
paths whose elevation differs from the ground model remain known limitations.

## First real preparation result

Prepared on 2026-10-06 from a bounded OpenStreetMap Overpass extract:

| Measurement | Result |
| --- | ---: |
| Source ways | 33,059 |
| Selected runnable ways | 31,555 |
| Graph vertices | 56,977 |
| Physical segments | 71,032 |
| Directed arcs | 142,064 |
| Physical segment length | 2,074.585 km |
| Undirected components | 364 |
| Vertices in largest component | 55,853 |
| Segments missing surface tags | 25,283 |
| Bridge / tunnel / stair segments | 312 / 368 / 867 |
| Terrain sample coverage | 100% |

The many small components and service segments are retained for inspection.
The largest component contains about 98% of graph vertices, so route search can
start there while later quality scoring penalizes driveways, parking aisles,
poor surfaces, and other unattractive choices. Missing OSM tags remain unknown,
not implicit evidence of a good surface or public access.

The current JSON graph is about 83 MB and the inspection GeoJSON about 54 MB.
They are acceptable experiment artifacts but not a final query format. A more
compact binary, SQLite, or GeoPackage representation should be considered only
after the first searches reveal the access patterns actually needed.

## Validation and next boundary

Synthetic tests cover junction splitting, non-junction geometric crossings,
pedestrian direction restrictions, private access rejection, start snapping,
GeoJSON output, and directional terrain integration. A local plot around
Ruusutorpan koulu was also inspected for plausible connectivity and path-class
distribution.

Step 2 provides a usable shared graph and measured limitations. Step 3 can now
add ordinary shortest-path search and the diverse-path baseline without yet
implementing face cycles.

OpenStreetMap data is © OpenStreetMap contributors and available under the
[ODbL](https://www.openstreetmap.org/copyright).

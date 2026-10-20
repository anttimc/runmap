# 13. Runnable-path graph prototype

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

## Scope

The first path graph supports the flat 10 km loop experiment defined in the
[historical route-planning experiment](../12-route-planning-design.md). Its preparation rectangle
is `E 373000–380000`, `N 6673000–6682000` in EPSG:3067. This covers the
Leppävaara/Kilo test neighborhood with context around its edges. Ring I is not a route boundary. The historical topology study is archived;
this graph now supports the [map-first plan](22-map-first-running-plan.md).

Ruusutorpan koulu is identified by its public address, Leppävaarankatu 24, and
an approximate landmark coordinate. Preparation snaps that coordinate to the
nearest graph vertex and records both the vertex and distance. The first real
extract snaps 24.2 m to a degree-two outdoor pedestrian-area boundary beside
the school.

## Preparation

```sh
PYTHONPATH=src .venv/bin/python -m runmap.cli paths prepare \
  leppavaara-flat-10k --dry-run
PYTHONPATH=src .venv/bin/python -m runmap.cli paths prepare \
  leppavaara-flat-10k
```

The bounded Overpass query downloads the initial runnable highway classes and
any configured route-boundary roads. This experiment currently has no hard
route boundary.
The raw response and provenance sidecar are stored under `data/paths/raw/`.
Prepared outputs under `data/paths/prepared/` are:

- `graph.json`: vertices, physical segments, directed arcs, route boundaries,
  source identity, start snap, terrain method, and summary;
- `segments.geojson`: physical geometry and attributes for map inspection;
- `inspection.json`: compact graph and start-snap summary.

These local data products are ignored by Git. `--input` can rebuild from an
existing Overpass JSON response without network access, and `--without-terrain`
can inspect topology before derived terrain tiles exist.

## Initial path policy

The importer accepts outdoor footways, paths, pedestrian and living streets,
pedestrian-permitted cycleways, residential and service streets, tracks,
steps, and unclassified roads. It rejects area geometry, indoor and
building-passage paths, parking aisles, enclosed service roads, unqualified
level-specific geometry, construction, explicit pedestrian denial, and
private/no access unless an explicit pedestrian permission overrides the
general access tag. A level-tagged pedestrian tunnel or bridge remains valid:
this distinguishes public underpasses from indoor floor plans. A cycleway also
requires explicit pedestrian permission; missing permission is not treated as
permission.

Outdoor `highway=pedestrian` and `highway=footway` areas are represented by
their perimeters in this linear graph. This deliberately approximates a
runner's freedom to cross a plaza, but it preserves the area's boundary connectivity. Indoor, level-specific, vehicle, and service
areas remain excluded. A later area-routing model can add direct interior
crossings between entrance points.

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
| Source ways | 33,160 |
| Selected runnable ways | 30,423 |
| Graph vertices | 55,627 |
| Physical segments | 69,707 |
| Directed arcs | 139,414 |
| Physical segment length | 2,036.809 km |
| Undirected components | 398 |
| Vertices in largest component | 54,394 |
| Segments missing surface tags | 24,718 |
| Bridge / tunnel / stair segments | 310 / 125 / 844 |
| Pedestrian-area boundary segments | 1,336 |
| Route-boundary ways | 0 |
| Terrain sample coverage | 100% |

The many small components and ordinary service segments are retained for
inspection. The largest component contains about 98% of graph vertices, so
route search can start there while later quality scoring penalizes driveways,
poor surfaces, and other unattractive choices. Parking aisles and enclosed
service circulation are excluded during import. Missing OSM tags remain
unknown, not implicit evidence of a good surface or public access.

The current JSON graph is about 82 MB and the inspection GeoJSON about 54 MB.
They are acceptable experiment artifacts but not a final query format. A more
compact binary, SQLite, or GeoPackage representation should be considered only
after the first searches reveal the access patterns actually needed.

## Validation and next boundary

Synthetic tests cover junction splitting, non-junction geometric crossings,
pedestrian direction restrictions, private access rejection, start snapping,
GeoJSON output, and directional terrain integration. A local plot around
Ruusutorpan koulu was also inspected for plausible connectivity and path-class
distribution.

Step 2 provides a usable shared graph and measured limitations. The implemented
[diverse-path baseline](14-diverse-route-baseline.md) now uses it for step 3.

OpenStreetMap data is © OpenStreetMap contributors and available under the
[ODbL](https://www.openstreetmap.org/copyright).


## Current configuration

Routing experiment schema 2 removes the retired topology settings. Rebuild
prepared graphs with the commands above after this change: configuration
digests intentionally invalidate the old graph. Cached OSM inputs can be reused.
Path-gradient UI delivery and reachability are planned in M4; this document
records the existing preparation prototype.

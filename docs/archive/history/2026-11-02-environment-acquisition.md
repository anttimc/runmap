# Leppävaara environment source acquisition, 2026-11-02

Pilot source gathering succeeded using OSMnx 2.1.1 and Overpass. This is evidence
of a usable internally consistent extract, not complete geographic mapping,
validated segmentation, or an environment preference delivered to routing.
Current commands and limitations belong in
[development](../../development.md#acquire-environment-pilot-data); outstanding
work belongs in the [roadmap](../../roadmap.md#planned-environment-preferences-experiment).

## Reproduction and identity

```sh
uv run --extra environment python -m runmap.sources.environment \
  config/environment/leppavaara-pilot.json
```

The configuration pins a 6 × 6 km EPSG:3067 core
`[374000, 6674000, 380000, 6680000]` and a 300 m halo. The enclosing WGS84 box is
approximately `[24.718822, 60.180579, 24.841822, 60.241788]` in
left/bottom/right/top order. Source objects extending beyond that box are retained
when selected. The query selects a union of environment tags, including all
highways independently of runnable eligibility, and does not geocode a place name.

Run from working-tree implementation based on revision `6ee79f6`, with acquisition
code, configuration, and the optional dependency/lock changes uncommitted.
Python was 3.14.6 on macOS ARM64. No model was trained and no field-generation
performance target was evaluated. `uv.lock` records the resolved dependencies.

Local Git-ignored output: `data/environment/raw/leppavaara-pilot/`.
The manifest records full query bounds, selectors, source timestamps,
checksums, licence, and geometry summaries. The OSM base timestamp was
`2026-11-02T07:36:17Z`.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Raw cached response | 35,790,157 | `a99f8eebec1a589dbd35f2397aa9583843b381a02c564f4c61b4a7cf99552105` |
| Compact `features.geojson` | 20,957,213 | `bdffe305267a9d1cbb2940bec0b9ce19e0454680207b6ced07b3e0b49ea37143` |

The initial sparse-table GeoJSON export included empty tag properties and was
regenerated from the same raw cache with missing properties omitted. The raw
response is unchanged. Feature IDs and available source tags remain in the compact
export; full original OSM records remain in the cache.

## Observations

The source response contained 347,029 distinct objects: 298,987 nodes, 47,914 ways,
and 128 relations. OSMnx returned 54,654 tagged features intersecting the query
box: 27,279 LineStrings, 20,193 Polygons, 11 MultiPolygons, and 7,171 Points.

| Tag category | Returned features |
| --- | ---: |
| Woodland (`natural=wood` or `landuse=forest`) | 933 |
| Water areas (`natural=water` or reservoir/basin land use) | 70 |
| Features with a `waterway` tag | 1,192 |
| Coastlines | 7 |
| Features with a `highway` tag, including points | 33,617 |
| Features with a `building` tag | 18,550 |
| Parks/nature reserves | 213 |

Categories overlap and do not sum to the feature count. Highway counts are not
road-edge counts. These are source features, not reviewed training labels.

Checks performed:

- All returned geometries passed Shapely validity checks. This does not prove
  their agreement with the ground or rule out repairs made during library assembly.
- Across all raw objects, every node referenced by a way was present; every
  typed member reference in a relation was present. Neither check discovers
  objects that the original bounding-box query never selected.
- Offline acquisition tests cover IDs/tags and polygon-hole preservation,
  omitted missing properties, request validation, interrupted-run identity,
  settings restoration, and completed-output overwrite protection.
- Existing CLI regression checks passed independently of this acquisition path.

The next evidence gate is visual/source review of woodland interiors and holes,
waterfronts, major-road surroundings, and enclosing polygons. Coastline lines
are not ocean-area masks. Spatial split assignment, independent labels,
baseline field generation, PyTorch comparisons, and runner acceptance remain open.

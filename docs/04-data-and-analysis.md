# 4. Data and analysis plan

## Source data

### OpenStreetMap

Initially use OSM as map context. Later use its geometry and connectivity to identify candidate runnable paths. Preserve access, surface, steps, bridge, and tunnel information where available. Missing attributes do not establish access or surface quality. Area-specific selection rules remain open.

### Open elevation

For Leppävaara, Espoo, Finland, use the National Land Survey of Finland (Maanmittauslaitos) Elevation model 2 m, subject to checking the exact study bounds and coverage.

The model uses a 2 m grid, EPSG:3067 horizontal coordinates, and N2000 heights. Grid spacing is not elevation accuracy. Keep analysis in an appropriate metre-based coordinate system and transform display geometry separately.

The dataset is CC BY 4.0. Record the provider, dataset name, acquisition date, licence link, and processing changes for attribution. Public and commercial reuse is permitted under its terms. OSM and any basemap have separate licensing/usage requirements to check before publication.

Source: [Elevation model 2 m and licence](https://www.maanmittauslaitos.fi/en/maps-and-spatial-data/datasets-and-interfaces/product-descriptions/elevation-model-2-m).

## Intermediate storage

| Stage | Initial format | Notes |
| --- | --- | --- |
| Raw inputs | Original GeoTIFF and OSM extract | Preserve sources; avoid committing large data |
| Prepared elevation | Tiled, compressed COG | Inspect existing layout before converting |
| Filtered elevation and terrain derivatives | Float32 COGs with valid-data masks | Cache by source, method, parameters, grid, and algorithm version |
| Candidate paths and analysis geometry | GeoPackage | Inspectable local spatial storage |
| Sampled profiles | Parquet | Path ID, sample order, distance, elevation, validity |
| Derived measurements | GeoPackage / Parquet | Geometry or profile-linked values as appropriate |
| Published display | Raster magnitude tiles plus direction features; later path vector tiles | Numeric analysis rasters remain separate from display assets |
| Provenance | JSON | Source identity, dates, CRS, parameters, code/algorithm version |

Cache keys must distinguish source versions and calculation settings. An algorithm change should invalidate derived metrics without unnecessarily invalidating sampled profiles. A sampling or elevation-source change must invalidate affected profiles as well.

COGs allow efficient partial raster reads. PMTiles allows partial HTTP reads of prepared tile archives and requires an archive rewrite for updates. Neither replaces the authoritative analysis profiles.

References: [GDAL COG](https://gdal.org/en/stable/drivers/raster/cog.html), [PMTiles](https://docs.protomaps.com/pmtiles/).

## Analysis choices

The first experiment is elevation-surface smoothing followed by terrain-gradient magnitude and direction. Compare against raw gradients. Details and proposed PDE formulation are in the [Leppävaara experiment](06-leppavaara-terrain.md); the final smoother is not selected.

The following path/route questions are deferred:

- Meaning of flatness and hilliness: local grade, sustained grade, total ascent, elevation range, or a combination.
- Sampling interval and interpolation method.
- Smoothing method and measurement window length.
- Rules for separating climbs, descents, and rolling terrain.
- Whether flatness uses absolute grade while climb inspection uses a selected travel direction.
- Treatment of stairs, bridges, tunnels, positional errors, and missing data.
- How results aggregate across junctions and at overview zoom levels.

Previously discussed values such as 5 m path sampling are examples only. Terrain magnitude is defined in the experiment; a route-hilliness score and smoothing thresholds remain open.

## Terrain slope versus path gradient

Terrain slope describes the steepness of the surrounding ground and is the first visualization. Path gradient describes elevation change along a path and remains necessary for the later running-specific goals.

Compute measurements from suitable analysis-resolution data. Simplifying map geometry or choosing a coarser display level must not silently redefine the metric. In particular, calculating slope after averaging elevations can hide short hills.

## Validation

For the first release use synthetic 2D surfaces and the magnitude/direction validation described in the [terrain experiment](06-leppavaara-terrain.md). The following profile checks apply when path analysis starts.

Use synthetic profiles with known properties: flat ground, constant rise, constant descent, a short climb, rolling terrain, missing elevations, and noisy flat ground. Verify units, direction, window boundaries, and behaviour at joins.

Compare a small set of familiar real locations against expected terrain. Treat recorded runs as additional evidence, not perfect elevation ground truth. Preserve quality flags so uncertainty can be shown in the UI.

## Performance experiment

A 20 km × 20 km region at 2 m spacing contains approximately 100 million cells, or 400 MB per uncompressed float32 band. This is a sizing calculation, not a runtime benchmark.

Measure separately:

- Cold and cached source reads.
- Path extraction and elevation sampling.
- Numerical analysis using cached profiles.
- Tile generation, transfer size, and rendering.
- Peak memory and map responsiveness at the maximum viewport.

Record machine specifications, dataset extent, path length/count, parameters, and whether caches were warm. Set performance targets after the first representative run.

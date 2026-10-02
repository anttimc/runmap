# 11. Region, elevation storage, and hosting design

Status: target design for expanding the single-raster prototype. Values marked
**provisional** must be validated during implementation; the source and cache
identities are requirements rather than suggestions.

## Purpose and scope

The first prototype loads one 6 × 6 km elevation raster into memory and returns
one georeferenced image. The next design must support:

- several disjoint named regions, including the Helsinki capital region and a
  private area near Jämsä;
- one local copy of overlapping MML elevation data rather than one copy per
  region;
- fast default rendering for a hosted service;
- bounded, cached calculation of alternative smoothing settings;
- later expansion without constructing one country-sized raster; and
- reproducible source versions, coverage manifests, and derived products.

A region is a coverage and presentation definition. It is not a raster file.
Source elevation, analysis results, and browser map tiles use separate grids and
lifecycles.

## Capacity model

At 2 m spacing there are 250,000 cells per square kilometre. One Float32 band
therefore occupies approximately 1 MB/km² before compression.

| Example | Area | Uncompressed elevation |
| --- | ---: | ---: |
| Current Leppävaara crop | 36 km² | 36 MB |
| One 5 × 5 km source tile | 25 km² | 25 MB |
| One 10 × 10 km WCS maximum request | 100 km² | 100 MB |
| A 60 × 60 km regional rectangle | 3,600 km² | 3.6 GB |
| Approximately 338,000 km² of land | 338,000 km² | 338 GB |

The current 36 MB uncompressed Leppävaara grid is a 26 MB downloaded GeoTIFF.
That observed ratio is useful for rough estimates only. Prepared layout,
terrain variability, masks, and compression settings change the result.

Capital-region and selected-location coverage is consequently small enough to
preload. Nationwide coverage is technically possible in object storage, but is
not required for the first hosted service.

## Three grids

### Region bounds

A version 1 region is an axis-aligned EPSG:3067 rectangle. Its bounds describe
the visible and authorized coverage, not how data is stored. Polygon clipping
can be added later without changing the source grid.

Bounds use `[west, south, east, north]` metres and half-open semantics:
`west <= E < east` and `south <= N < north`. Every bound must be finite and
aligned to the native 2 m elevation grid.

### Source and analysis grid

Source and analysis tiles use a global 5,000 m grid in EPSG:3067. Grid lines
occur where both coordinates are integer multiples of 5,000 m. A tile is
identified by its southwest corner:

```text
e0370000_n6675000
```

The identifier above covers `[370000, 6675000, 375000, 6680000)`. Coordinates
are zero-padded to seven digits only to keep filenames sortable; the numeric
coordinate is authoritative.

Tile selection is deterministic:

```text
west  = floor(E / 5000) * 5000
south = floor(N / 5000) * 5000
```

The 5 km size is below MML's 10 × 10 km native-resolution WCS limit, contains
6.25 million cells, aligns exactly to the 2 m grid, and gives bounded processing
jobs. Regions share tiles through a catalog, so overlapping regions do not
duplicate downloads.

### Browser delivery grid

The browser uses standard 256 × 256 Web Mercator XYZ tiles. Reprojection from
EPSG:3067 happens only during display-tile generation. Scientific smoothing and
differentiation remain on the native, metre-based grid.

The planned terrain zoom range is 8–15. Near 60° north, zoom 15 is close to the
native 2 m ground resolution. Higher zooms may overzoom the zoom-15 terrain
tile while MapLibre continues to request more detailed vector-map geometry.

## Region configuration

Public definitions live under `config/regions/`. Private definitions, such as
an exact cottage location, should live in a separately ignored deployment
directory and must not be committed accidentally.

Example using the existing extent:

```json
{
  "schema_version": 1,
  "id": "leppavaara",
  "title": "Leppävaara, Espoo",
  "bounds_epsg3067": [374000, 6676000, 380000, 6682000],
  "acquisition_policy": "preload",
  "public": true,
  "initial_view": {
    "zoom": 13
  },
  "attribution_label": "Elevation model 2 m"
}
```

| Parameter | Type/default | Meaning |
| --- | --- | --- |
| `schema_version` | integer, `1` | Region-file schema; reject unsupported versions. |
| `id` | lowercase slug, required | Stable URL/catalog identity; changing it creates a new region. |
| `title` | string, required | Human-readable UI name. |
| `bounds_epsg3067` | four numbers, required | Visible region rectangle in metres. |
| `acquisition_policy` | `preload` by default | `preload`, `admin-request`, or future `public-request`. |
| `public` | boolean, `true` | Whether the API lists and serves this region without private authorization. |
| `initial_view.zoom` | number, derived if absent | Initial MapLibre zoom; center is derived from bounds unless supplied later. |
| `attribution_label` | string | Dataset label shown with common provider/date attribution. |

The capital-region bounds must be selected explicitly rather than inferred
from an ambiguous administrative term. The Jämsä definition needs the desired
private bounds; only its tile identifiers and server-side configuration are
needed at runtime.

Acquisition first selects every 5 km analysis core intersecting the region,
expands each complete core by the global processing halo, and then selects all
source tiles intersecting those expanded windows. It must not simply buffer the
visible bounds: if a region ends partway through an analysis core, that shortcut
can omit a neighboring tile needed by the far edge of the core. Source tiles
outside the visible region may therefore be shared and retained solely as
processing context.

## Global elevation configuration

Behavioral settings belong in a versioned project configuration. Secrets and
deployment paths belong in environment variables.

| Parameter | Initial value | Status and purpose |
| --- | --- | --- |
| `provider` | `mml` | National Land Survey of Finland. |
| `wcs_endpoint` | `https://avoin-karttakuva.maanmittauslaitos.fi/ortokuvat-ja-korkeusmallit/wcs/v2` | Provider endpoint; credentials are sent in the authorization header. |
| `wcs_version` | `2.0.1` | Protocol version used by the existing downloader. |
| `dataset` / WCS coverage ID | `korkeusmalli_2m` | MML Elevation model 2 m. |
| `horizontal_crs` | `EPSG:3067` | ETRS89 / TM35FIN analysis grid. |
| `vertical_crs` | `EPSG:3900` | N2000 heights; preserve in provenance. |
| `source_resolution_m` | `2` | Native analysis resolution. |
| `source_tile_size_m` | `5000` | Shared acquisition and analysis core grid. |
| `source_tile_pixels` | `2500` | Derived from size/resolution; validate rather than configure independently. |
| `wcs_max_request_size_m` | `10000` | Provider constraint, not a chosen tile size. |
| `download_format` | GeoTIFF with LZW | Exact MML response retained as raw input. |
| `download_timeout_s` | `180` | Existing downloader behavior. |
| `download_attempts` | `3` | **Provisional** retry count for transient failures. |
| `download_backoff_s` | `1, 4` | **Provisional** waits before attempts two and three; never retry validation failures blindly. |
| `download_concurrency` | `2` | **Provisional** conservative provider concurrency. |
| `refresh_after_days` | `365` | Check policy; never silently replaces a snapshot. |
| `prepared_format` | COG | Window-readable canonical elevation tile. |
| `prepared_data_type` | Float32 | Preserve delivered numeric precision. |
| `prepared_block_size_px` | `512` | Internal COG block size. |
| `prepared_compression` | ZSTD level 9 | **Provisional**; benchmark against DEFLATE/LZW. |
| `prepared_predictor` | floating point | Compression predictor suitable for Float32 elevation. |
| `prepared_overviews` | `2, 4, 8, 16, 32` | Inspection/display reads only, never a silent analysis-resolution change. |

Environment variables:

| Variable | Required | Meaning |
| --- | --- | --- |
| `MML_API_KEY` | acquisition only | Secret sent by the backend downloader, never to the browser. |
| `RUNMAP_DATA_ROOT` | no | Root for raw, prepared, and derived data; defaults to project `data/`. |
| `RUNMAP_CATALOG_PATH` | no | Catalog location; defaults below the data root. |
| `RUNMAP_PUBLIC_ACQUISITION` | no | Defaults to `false`; see acquisition policy below. |

## Source versions, coverage manifests, and catalog

An MML WCS response has a delivery time rather than a convenient immutable
dataset version. Runmap assigns each download campaign an acquisition-batch ID
such as `mml-dem2m-2026-10-02`. Each source tile also records its own exact
retrieval time and checksum.

An immutable coverage manifest maps tile IDs to exact acquisition-batch tile
versions. Extending coverage creates a new manifest that reuses existing tile
references and adds newly downloaded tiles; it does not copy or redownload the
old bytes. Refreshing one changed tile likewise creates a new tile version and
manifest while keeping the earlier version reproducible. A digest of the
ordered tile/checksum mapping identifies the manifest.

```text
data/elevation/
  catalog.sqlite
  raw/<acquisition-batch>/<tile-id>.tif
  raw/<acquisition-batch>/<tile-id>.tif.json
  prepared/<acquisition-batch>/<tile-id>.tif
  manifests/<coverage-manifest-id>.json
  derived/<source-window-digest>/<algorithm-version>/<setting>/<tile-id>.tif
  display/<region-scope>/<render-version>/<setting>/<z>/<x>/<y>.png
```

Raw WCS responses and their provenance sidecars are immutable. Prepared COGs
can be rebuilt from raw data. Derived and display data are caches and can be
deleted and regenerated.

The initial SQLite catalog contains these logical records:

- `regions`: ID, schema version, bounds, policy, visibility, active coverage
  manifest, and configuration digest;
- `acquisition_batches`: provider, dataset, campaign date, CRS, resolution,
  licence, and status;
- `source_tiles`: acquisition batch, tile ID, exact retrieval time, bounds,
  paths, byte counts, SHA-256, validation results, and status;
- `coverage_manifests`: immutable ID, ordered mapping digest, creation time,
  and status;
- `coverage_manifest_tiles`: manifest/tile/version mappings;
- `region_source_tiles`: required tile IDs independent of a particular
  version;
- `derived_tiles`: cache identity, core bounds, source checksums, parameters,
  path, byte count, and status;
- `jobs`: acquisition/processing type, target, state, attempts, timestamps, and
  diagnostic message.

Tile states are `missing`, `downloading`, `validating`, `ready`, `failed`, or
`stale`. Downloads use a unique `.part` file and an inter-process tile lock.
Only a validated file is atomically promoted to `ready`. A ready matching tile
is never downloaded twice.

## Acquisition policy

Known regions are prepared explicitly:

```text
runmap region prepare leppavaara
runmap region verify leppavaara
runmap region precompute leppavaara --wavelength-m 40
```

These commands describe the intended interface; their exact CLI spelling is
not implemented yet.

Normal browser panning must not contact MML. The initial public policy is:

- capital-region and other published coverage: `preload`;
- private/special locations: administrator-created `admin-request` jobs;
- anonymous `public-request`: disabled;
- successful source downloads: retained permanently;
- acquisition failures: reported as unavailable coverage, never as flat land.

If public acquisition is enabled later, it needs authentication or rate limits,
an area quota, deduplication through the fixed grid, and a total storage budget.
Those product limits are intentionally not selected while the feature is
disabled.

MML states that the product is updated when needed following new laser
scanning, and quality class II can later be replaced by class I. A yearly check
is sufficient for the initial service, but publishing a new coverage manifest
remains a manual, observable operation. Only derived tiles whose contributing
source checksums changed are invalidated.

## Analysis tiling and boundary handling

Each 5 km analysis job reads its core plus a halo from adjacent source tiles:

```text
5,000 m core
  + 512 m halo on every side
  = 6,024 m / 2 m = 3,012 pixels per side
```

The complete haloed array is smoothed and differentiated in EPSG:3067. The halo
is then discarded and only the 5 km core is stored. The initial worker
concurrency is one because the DCT and its Float64 work arrays can use several
hundred megabytes for a 3,012 × 3,012 window.

| Parameter | Initial value | Meaning |
| --- | --- | --- |
| `analysis_tile_size_m` | `5000` | Same core grid as source tiles. |
| `processing_halo_m` | `512` | **Provisional** boundary context for all supported wavelengths. |
| `analysis_worker_concurrency` | `1` | Initial per-process numerical concurrency. |
| `smoother` | `spectral-thin-plate` | DCT/Neumann biharmonic implementation. |
| `smoother_version` | explicit code version, initially `1` | Required cache-key component. |
| `default_wavelength_m` | `40` | Half-amplitude wavelength, not kernel diameter. |
| `hosted_wavelengths_m` | `0, 20, 40, 60, 80, 100` | Public presets; `0` means raw elevation. |
| `precomputed_wavelengths_m` | `40` | Full-region eager build. Other presets are lazy. |
| `derivative_method` | NumPy central/interior, one-sided/edge | Existing metric-aware implementation. |
| `slope_unit` | rise/run fraction | Multiply by 100 only for percent display. |
| `direction` | degrees clockwise from EPSG:3067 grid north | Uphill bearing; undefined at zero magnitude. |

The local single-raster prototype may retain its 0–100 m slider in 2 m steps.
The hosted API advertises the finite preset list so cache growth is predictable.
Adding a preset changes configuration, not the mathematical definition.
The continuous and discrete formulation, including the actual DCT boundary
operator, is derived in [Thin-plate smoothing and its DCT
implementation](10-thin-plate-dct-mathematics.md).

The 512 m halo is deliberately conservative but not yet proven. Validate it by
comparing tiled output with a larger single-domain calculation for every hosted
wavelength. Inspect error inside the core and across joins. Increase the halo
or change the solver if visible or numerically material seams remain.

The current spectral implementation requires every cell in its processing
window to be finite. Acquisition validation must distinguish true provider
nodata from valid zero-height terrain. Until a masked thin-plate method or an
explicit, validated fill policy exists, a window containing nodata is marked
unrenderable rather than treating nodata as zero.

## Derived analysis cache

The recommended display-oriented derived COG has two UInt16 bands:

1. slope magnitude as rise/run with scale `0.0001`;
2. uphill bearing with scale `0.01` degree.

`65535` is nodata in both bands. Valid slope values therefore cover 0 through
6.5534 (0–655.34% grade), and bearings use 0 through 35999. Any overflow must
be flagged rather than silently wrapped. This quantized product is a
regenerable display cache, not the authoritative numerical result.

If later profile analysis needs filtered elevation or east/north gradient
components, add a separate Float32 analysis product. Do not redefine this
display cache or use its quantization silently for scientific calculations.

A derived cache identity contains all of:

- source dataset;
- ordered source tile IDs and SHA-256 values;
- analysis grid and 2 m resolution;
- smoother name and version;
- half-amplitude wavelength;
- processing halo and boundary convention;
- derivative implementation version;
- band encoding and scale version; and
- code/configuration digest where the preceding identities are insufficient.

Source tiles and the full default 40 m derived coverage are retained. Lazy
non-default derived tiles use a **provisional** 20 GiB local LRU budget and a
30-day minimum age before eviction. A hosted object-store lifecycle may replace
that local policy, but source data is never part of the eviction set.

## Display tiles and HTTP API

Planned read endpoints:

```text
GET /api/regions
GET /api/regions/{region_id}
GET /api/regions/{region_id}/coverage
GET /api/regions/{region_id}/terrain/{render_version}/w{wavelength_m}/{z}/{x}/{y}.png
```

The region in the URL enforces visible coverage and access policy. Internally,
derived analysis tiles remain region-independent and can be shared by
overlapping regions.

| Parameter | Initial value | Meaning |
| --- | --- | --- |
| `display_crs` | `EPSG:3857` | Standard XYZ/Web Mercator delivery. |
| `display_tile_size_px` | `256` | Browser raster tile size. |
| `display_min_zoom` | `8` | Coarsest terrain level. |
| `display_max_zoom` | `15` | Approximately native ground resolution near 60°N. |
| `display_overzoom_max` | `18` | Reuse z15 terrain above native resolution. |
| `display_metatile_size` | `2 × 2` | **Provisional** render/cache-fill unit. |
| `display_resampling` | bilinear | Display only; never changes analysis metrics. |
| `display_format` | indexed PNG | Lossless output and transparent unavailable cells. |
| `png_compress_level` | `4` | Existing encoder setting; benchmark before changing the render version. |
| `slope_color_max` | `0.40` | Existing legend cap: 0.40 rise/run = 40%. |
| `overlay_opacity_default` | `0.78` | Client-side MapLibre opacity; not part of the tile cache identity. |
| `overlay_opacity_range` | `0–1`, step `0.01` | Presentation control only. |
| `render_version` | explicit, initially `1` | Includes palette, transparency, resampling, and encoder. |
| successful cache control | one year, immutable | Safe because version and setting are in the URL. |
| unavailable coverage | transparent tile or `204` | Must not appear as zero slope. Exact response chosen during API implementation. |

The initial slope palette is retained from the prototype and linearly
interpolated in slope-percent space:

| Grade | Color |
| ---: | --- |
| 0% | `#111827` |
| 2% | `#183d59` |
| 5% | `#087f8c` |
| 10% | `#f4d35e` |
| 20% | `#ee964b` |
| 40% and above | `#d1495b` |

Palette index zero is transparent nodata. Valid values occupy the remaining
255 indices. Changing stops, interpolation, clipping, transparency, or encoder
settings requires a new `render_version`.

The display cache key includes region access scope, render version, wavelength,
z/x/y, and the identities of contributing derived tiles. A palette change
invalidates display tiles but not elevation or derived slope/bearing COGs.

At wide zooms, resampling is a visual summary and can hide short steep
features. It must not be presented as a new measurement. Later overview styles
may deliberately encode maxima or hill prominence, but require separate names
and validation.

## Hosted execution model

The default public path is:

```text
MML acquisition job (infrequent)
  -> immutable raw GeoTIFF in object storage
  -> validated 5 km elevation COG
  -> default 40 m derived analysis COG
  -> versioned XYZ display tile
  -> object storage/CDN
  -> MapLibre
```

For a non-default hosted preset, a cache miss queues or performs the bounded
5 km analysis jobs needed by the requested view, stores their results, and then
renders display tiles. The API should return an explicit pending response if
the work exceeds the interactive request budget; it must not start an
unbounded regional calculation in a web worker.

Raw and prepared data may be stored in S3-compatible object storage. Processing
workers should maintain a bounded local disk cache for source COG blocks or
whole 5 km tiles so repeated FFT work does not repeatedly fetch remote ranges.
Default display tiles sit behind a CDN. SQLite is sufficient on one machine;
a shared transactional database and job queue become necessary only when
multiple workers must coordinate writes.

## Attribution and provenance

The MML Elevation model 2 m is open data under CC BY 4.0. Published output must
identify the National Land Survey of Finland, dataset, and relevant delivery
date or date range. Preserve the horizontal EPSG:3067 and vertical N2000
definitions, exact request bounds, request URL without credentials, checksums,
coverage-manifest ID, processing parameters, and algorithm/render versions.

References:

- [MML Elevation model 2 m](https://www.maanmittauslaitos.fi/en/maps-and-spatial-data/datasets-and-interfaces/product-descriptions/elevation-model-2-m)
- [MML WCS technical description](https://www.maanmittauslaitos.fi/ortokuvien-ja-korkeusmallien-kyselypalvelu/tekninen-kuvaus)
- [MML download and updating services](https://www.maanmittauslaitos.fi/en/e-services/mapsite/related-services)
- [GDAL Cloud Optimized GeoTIFF](https://gdal.org/en/stable/drivers/raster/cog.html)

OSM and the selected production basemap retain separate attribution, storage,
and service-policy requirements.

## Implementation sequence

1. Introduce versioned region files and validate IDs, bounds, privacy, and grid
   alignment.
2. Implement the global 5 km tile index and SQLite catalog.
3. Extend acquisition to prepare all missing buffered tiles atomically and
   convert validated responses to COGs.
4. Read a region through the tile catalog/VRT while retaining the current
   single-raster endpoint as a reference.
5. Implement haloed 5 km analysis tiles and validate them against whole-domain
   results, especially at joins.
6. Add derived encoding, versioned XYZ rendering, and the new region endpoints.
7. Preload and precompute the chosen capital-region bounds and add the private
   Jämsä configuration outside version control.
8. Add object-storage/CDN adapters only after the local tiled pipeline and cache
   identities pass integration tests.

## Acceptance criteria

- Two overlapping regions reference one copy of each matching source tile.
- Re-running preparation performs no network transfer for ready matching tiles.
- Every published pixel can be traced to source checksums and processing
  parameters.
- An acquisition batch or coverage manifest is never silently modified in
  place.
- Default 40 m navigation needs no live MML request or full-region FFT.
- Non-default work is bounded by fixed analysis tiles and a finite preset list.
- Tiled 40 m results agree with a larger-domain reference within measured,
  documented tolerances and show no visible joins.
- Missing/nodata/source failures remain distinguishable from zero elevation or
  flat terrain.
- Private region coordinates are not exposed through public configuration,
  logs, or repository history.
- Deleting derived/display caches and rebuilding produces equivalent results
  for the same source and versioned parameters.

## Parameters still requiring measurement or user input

- Exact EPSG:3067 bounds for the capital-region product.
- Exact private Jämsä bounds and whether that region is ever published.
- Numerical seam tolerance and whether 512 m is a sufficient halo at 100 m.
- COG compression choice after size/read-speed benchmarks.
- Peak memory and latency for one haloed 5 km analysis tile.
- Whether six hosted wavelength presets are sufficient in actual use.
- Storage/CDN provider and deployment-specific cache budget.
- Response contract for a valid but not-yet-generated non-default tile.
- Policy for genuine MML nodata before extending into affected locations.

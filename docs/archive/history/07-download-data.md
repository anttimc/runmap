# 7. Download elevation data

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

## Access

Automated WCS and OGC API Processes downloads require a free personal Maanmittauslaitos API key. Create one through [My Account instructions](https://www.maanmittauslaitos.fi/en/rajapinnat/api-avaimen-ohje).

Manual [MapSite downloads](https://www.maanmittauslaitos.fi/en/e-services/mapsite) of this open dataset do not require registration. The repeatable application path uses the API and shared local tile store below.

The reusable downloader uses the [documented WCS service](https://www.maanmittauslaitos.fi/ortokuvien-ja-korkeusmallien-kyselypalvelu/tekninen-kuvaus). WCS supports an exact rectangle rather than requiring discovery of map-sheet file names. Each native-resolution elevation request is limited to 10 km per side; larger regions need multiple requests.

## Local setup and commands

The legacy one-crop downloader needs only Python 3.9+ and its standard library.
Named-region preparation uses the project's Rasterio/GDAL stack for validation
and COG creation.

Copy `.env.example` to `.env` and enter your own key. `.env` is ignored by Git. Never commit it or paste the key into chat. Environment variable `MML_API_KEY` takes precedence over `.env`.

Run from the repository root:

```sh
PYTHONPATH=src .venv/bin/python -m runmap.cli region list
PYTHONPATH=src .venv/bin/python -m runmap.cli region prepare leppavaara --dry-run
PYTHONPATH=src .venv/bin/python -m runmap.cli region prepare leppavaara
PYTHONPATH=src .venv/bin/python -m runmap.cli region precompute leppavaara --wavelength-m 40
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

The original comparison raster remains under `data/raw/leppavaara/`. The active
application region is defined in `config/regions/leppavaara.json` as a
grid-aligned 40 × 40 km EPSG:3067 rectangle: `E 360000–400000`,
`N 6660000–6700000`. It contains 400 million 2 m cells and resolves to 64
complete 5 × 5 km analysis cores. A 512 m halo around those cores expands
acquisition to 100 shared source tiles; 36 are processing context outside the
visible region.

The legacy command outputs `data/raw/leppavaara/elevation-2m.tif` and a JSON provenance sidecar containing acquisition time, bounds, licence, SHA-256, and a credential-free URL. Existing files are never overwritten. Failed transfers discard their partial output. The API key is sent only in an HTTPS authorization header; redirects are refused.

Prepare the region using `MML_API_KEY` from the environment or `.env`:

```sh
PYTHONPATH=src .venv/bin/python -m runmap.cli region prepare leppavaara
```

Preparation downloads only source tiles not already ready in the catalog,
validates their CRS, bounds, resolution, finite coverage, and dimensions,
converts them to tiled ZSTD COGs, and activates an immutable manifest. The
layout is under `data/elevation/`; it is excluded from Git. Repeating the
command reuses all ready source tiles without contacting MML.

Precomputation reads each 5 km core plus its halo, applies the selected spectral
thin-plate smoothing and differentiation at the native 2 m resolution, discards
the halo, and stores a two-band UInt16 derived COG. The default `40 m` setting
is built eagerly; the web service generates the other advertised presets only
when requested.

## Before analysis

The low-level downloader checks the TIFF signature and transfer length, not the
raster contents. Named-region preparation additionally checks CRS, exact tile
bounds, transform, dimensions, 2 m spacing, and finite/nodata coverage before
cataloging a tile as ready. This remains structural validation rather than an
independent assessment of terrain accuracy.

## Download and inspection result — 2026-09-05

Downloaded `data/raw/leppavaara/elevation-2m.tif` (27,212,355 bytes) with a provenance sidecar. Structural inspection passed: EPSG:3067, requested bounds exactly matched, 3000 × 3000 float32 cells, 2 m spacing, north-up orientation. All 9 million cells are finite and unmasked; the file declares no nodata value. This is not independent verification of terrain accuracy or undocumented missing-data sentinels.

Elevation range: approximately 0.183–64.286 m; mean 28.384 m. The response is strip-organized and has no overviews. Keep this original unchanged; prepare a tiled COG when adding efficient map delivery.

On 2026-10-05 the named region and its processing context were prepared as 16
source tiles. Raw responses occupy about 282 MB and prepared COGs about 282 MB.
All 16 contain complete finite 2 m grids. The four default 40 m derived COGs
occupy about 112 MB, for a roughly 676 MB baseline under `data/elevation/`
immediately after the default precompute. Lazy presets add roughly
the same derived amount per fully generated wavelength. Active manifest:
`leppavaara-a517c9ae0f51a046`.

The reusable inspector and its local environment can be run with:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-inspection.txt
PYTHONPATH=src .venv/bin/python -m runmap.elevation.inspect data/raw/leppavaara/elevation-2m.tif --report data/raw/leppavaara/inspection.json
```

`requirements-inspection.txt` records the tested Python 3.9/macOS environment. The JSON report contains structural checks, valid-cell counts, elevation statistics, and geographic bounds. The download checksum remains in the provenance sidecar.

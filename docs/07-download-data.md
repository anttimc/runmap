# 7. Download elevation data

## Access

Automated WCS and OGC API Processes downloads require a free personal Maanmittauslaitos API key. Create one through [My Account instructions](https://www.maanmittauslaitos.fi/en/rajapinnat/api-avaimen-ohje).

Manual [MapSite downloads](https://www.maanmittauslaitos.fi/en/e-services/mapsite) of this open dataset do not require registration. Choose Elevation model 2 m / GeoTIFF, select Leppävaara, and put extracted GeoTIFFs under `data/raw/leppavaara/`.

The reusable downloader uses the [documented WCS service](https://www.maanmittauslaitos.fi/ortokuvien-ja-korkeusmallien-kyselypalvelu/tekninen-kuvaus). WCS supports an exact rectangle rather than requiring discovery of map-sheet file names. Each native-resolution elevation request is limited to 10 km per side; larger regions need multiple requests.

## Local setup and commands

The downloader needs only Python 3.9+ and its standard library. It does not install the future raster-processing stack.

Copy `.env.example` to `.env` and enter your own key. `.env` is ignored by Git. Never commit it or paste the key into chat. Environment variable `MML_API_KEY` takes precedence over `.env`.

Run from the repository root:

```sh
PYTHONPATH=src python3 -m runmap.cli --dry-run
PYTHONPATH=src python3 -m runmap.cli
PYTHONPATH=src python3 -m unittest discover -s tests
```

The provisional bounds in `config/leppavaara.json` are a 6 × 6 km EPSG:3067 rectangle covering Leppävaara and surroundings. This is an experiment extent, not the administrative boundary. At 2 m resolution it is approximately 9 million cells / 36 MB uncompressed for one float32 band; download size depends on compression.

Output: `data/raw/leppavaara/elevation-2m.tif` and a JSON provenance sidecar containing acquisition time, bounds, licence, SHA-256, and a credential-free URL. Existing files are never overwritten. Failed transfers discard their partial output. The API key is sent only in an HTTPS authorization header; redirects are refused.

## Before analysis

The downloader checks the TIFF signature and transfer length, not the raster contents. Once downloaded, inspect CRS, bounds, transform, dimensions, 2 m spacing, nodata/valid coverage, and elevation range using Rasterio/GDAL. Then choose an interior display crop with adequate smoothing context. No experiment should use the file until that inspection succeeds.

## Download and inspection result — 2026-09-05

Downloaded `data/raw/leppavaara/elevation-2m.tif` (27,212,355 bytes) with a provenance sidecar. Structural inspection passed: EPSG:3067, requested bounds exactly matched, 3000 × 3000 float32 cells, 2 m spacing, north-up orientation. All 9 million cells are finite and unmasked; the file declares no nodata value. This is not independent verification of terrain accuracy or undocumented missing-data sentinels.

Elevation range: approximately 0.183–64.286 m; mean 28.384 m. The response is strip-organized and has no overviews. Keep this original unchanged; prepare a tiled COG when adding efficient map delivery.

The reusable inspector and its local environment can be run with:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-inspection.txt
PYTHONPATH=src .venv/bin/python -m runmap.elevation.inspect data/raw/leppavaara/elevation-2m.tif --config config/leppavaara.json --report data/raw/leppavaara/inspection.json
```

`requirements-inspection.txt` records the tested Python 3.9/macOS environment. The JSON report contains structural checks, valid-cell counts, elevation statistics, and geographic bounds. The download checksum remains in the provenance sidecar. Smoothing and gradient experiments have not run yet.

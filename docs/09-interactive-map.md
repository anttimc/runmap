# 9. Interactive terrain map

Status: first local prototype implemented. It displays slope magnitude for the active 10 × 10 km Leppävaara region and updates it when the thin-plate half-amplitude wavelength changes.

## Interaction

- Spectral biharmonic thin-plate slider with hosted presets at raw (`0 m`),
  `20 m`, `40 m`, `60 m`, `80 m`, and `100 m`.
- Default half-amplitude wavelength: `40 m`.
- Debounced overlay updates; changing the setting replaces the raster tile URL
  and MapLibre requests only tiles needed by the view.
- Slope-magnitude legend capped at 40% and overlay opacity control.
- The map initially fits the displayed source coverage; users can pan and zoom normally.
- A deliberately restrained OpenStreetMap Shortbread vector basemap, with visible contributor attribution.
- Neutral land, building, and road colors plus subdued blue-gray water avoid competing with the terrain gradient's blue, teal, yellow, orange, and red scale.
- Basemap geometry is below the terrain image while vector labels remain above it for legibility.
- At wider views, available major through residential roads are redrawn above the terrain as neutral context and fade out by zoom 13.5.
- Runner-relevant OSM pedestrian roads, tracks, footways, steps, paths, and cycleways are redrawn as high-contrast neutral dashed lines above the terrain image; features explicitly tagged `foot=no` are excluded. Pedestrian roads are widest. Paths tagged with maintained/hard surfaces (`gravel`, `fine_gravel`, `compacted`, paving, concrete, asphalt, or wood) or track grades 1–2 use a medium long-dash style. Because sampled Leppävaara Shortbread tiles contain surface or track-grade metadata for only about 1% of runner-network features, untagged `footway`, `cycleway`, and `track` features also use the maintained candidate style. Explicit dirt/earth/ground/grass/mud/sand surfaces and track grades 3–5 override that fallback. Remaining `path` and `steps` features use the thinnest short-dash style. These are visual candidates inferred from OSM tags, not verified maintenance or current conditions.
- Optional MapLibre style URL or vector TileJSON override for switching providers without changing source code.

`40 m` is a frequency-response parameter, not a kernel diameter or a claim that 40 m terrain features are noise. A sinusoidal elevation component at that wavelength is attenuated to approximately half amplitude.

## Execution model

At API startup, the backend opens the active region manifest without assembling
the entire region or calculating a full-region DCT. Analysis is divided into
four 5 km cores. Each derived job reads a 512 m halo from the surrounding
prepared COGs, smooths and differentiates the 3,012 × 3,012 native-resolution
window, discards the halo, and stores slope magnitude and uphill bearing in a
two-band UInt16 COG. The default 40 m coverage is precomputed; other finite
presets are created lazily per analysis core.

For a browser request, the backend reads the contributing derived COGs,
reprojects their slope magnitude from EPSG:3067 into one 256 × 256 Web Mercator
XYZ tile, and returns an indexed PNG. MapLibre displays these through a raster
tile source. A 128-entry in-process PNG LRU avoids repeated rendering during a
server run; the immutable URL and response headers also permit browser or CDN
caching.

The old single-raster renderer remains available when `RUNMAP_ELEVATION_PATH`
is set. It uses an image source whose four EPSG:3067 raster corners are
transformed individually; this avoids the roughly 200 m displacement caused by
using the corners of a transformed bounding envelope.

Endpoints:

- `GET /api/config` — region, manifest, bounds, preset values, legend, and XYZ
  tile template.
- `GET /api/regions/{region_id}/terrain/v1/w{wavelength_m}/{z}/{x}/{y}.png` —
  versioned 256 × 256 slope-magnitude tile for zooms 8–15.
- `GET /api/terrain.png?wavelength_m=40&max_size=1600` — legacy whole-raster
  image, available only with `RUNMAP_ELEVATION_PATH`.

Terrain responses include `X-Runmap-Compute-Ms`, `X-Runmap-Wavelength-M`, and
`X-Runmap-Manifest` headers. A tile from the in-process cache reports zero
compute milliseconds. Versioned tile responses use a one-year immutable cache
policy.

## Install and run

Python backend:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[web]'
PYTHONPATH=src .venv/bin/python -m uvicorn runmap.api.app:app --reload
```

Frontend development server, in a second terminal:

```sh
cd web
npm install
npm run dev
```

Open the Vite URL printed by the command. It proxies `/api` to `127.0.0.1:8000`.

For a single production-style local server:

```sh
cd web
npm run build
cd ..
PYTHONPATH=src .venv/bin/python -m uvicorn runmap.api.app:app
```

FastAPI serves `web/dist` when it exists. Set `RUNMAP_ELEVATION_PATH` to use another complete single-band raster. Otherwise the selected named region must have been prepared with the region command. Set `VITE_MAP_STYLE_URL` when building or running Vite to replace the default OpenStreetMap vector basemap with a complete MapLibre style:

By default the backend selects region `leppavaara` from `config/regions` and
reads `data/elevation`. `RUNMAP_REGION_ID`, `RUNMAP_REGION_DIRECTORY`, and
`RUNMAP_DATA_ROOT` override those locations. `RUNMAP_ELEVATION_PATH` takes
precedence and retains the old single-raster development path.

```sh
cd web
VITE_MAP_STYLE_URL='https://example.test/style.json' npm run dev
```

`VITE_OSM_VECTOR_TILEJSON_URL` can instead replace the Shortbread-compatible vector TileJSON endpoint. If a different provider is used, update its attribution as required rather than assuming the OpenStreetMap-only attribution is sufficient.

The default `vector.openstreetmap.org` service is appropriate only for modest, human-driven use under its [published vector tile policy](https://operations.osmfoundation.org/policies/vector/). Do not add prefetching, bulk downloads, offline caching, or use it as an assumed production SLA. Browsers should retain normal referer behavior and honour tile caching headers. Select a suitable provider or self-host before public deployment. The style targets the [Shortbread 1.1 schema](https://shortbread-tiles.org/schema/1.1/); a replacement endpoint must expose the same layer names and attributes.

## Measured local behavior

On the current machine and 3000 × 3000 source:

- Startup raster read and spectral preparation: approximately 0.26 s.
- Default 40 m smoothing, differentiation, 1600 px resize, and indexed PNG encoding: approximately 0.22 s.
- Default 1600 × 1600 PNG: approximately 1.7 MB.
- Repeated cached setting: no numerical recomputation.

For the tiled 10 km named region, one observed local precomputation of all four
40 m analysis cores took approximately 3.9 s after source preparation. A tested
zoom-12 XYZ tile rendered in approximately 0.26 s and produced a 57 KB PNG;
an in-process repeat requires no numerical or PNG recomputation. These remain
development observations, not guarantees.

## Current limitations and next slice

- Dynamic output currently shows magnitude only. Sparse uphill arrows and click inspection follow next.
- A first request for a non-default preset can perform a synchronous 5 km
  analysis job. A hosted deployment should precompute popular presets or move
  longer misses into a bounded job queue.
- The DCT reflecting boundary can affect the outside edge of available source
  context. The 512 m halo removes visible internal joins for the tested 40 m
  setting, but the 100 m preset still needs the same numerical seam check.
- The slider uses server-rendered colours. Independent client-side recolouring would require sending numerical data or a custom texture/shader representation.

The storage and cache identities behind this implementation are documented in
the [region and hosting design](11-region-and-hosting-design.md).

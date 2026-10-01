# 9. Interactive terrain map

Status: first local prototype implemented. It displays slope magnitude for the 6 × 6 km Leppävaara raster and updates it when the thin-plate half-amplitude wavelength changes.

## Interaction

- Spectral biharmonic thin-plate slider from raw (`0 m`) to `100 m`, in 2 m steps.
- Default half-amplitude wavelength: `40 m`.
- Debounced overlay updates; MapLibre cancels an older image-source request when a new URL replaces it.
- Slope-magnitude legend capped at 40% and overlay opacity control.
- The map initially fits the displayed source coverage; users can pan and zoom normally.
- A deliberately restrained OpenStreetMap Shortbread vector basemap, with visible contributor attribution.
- Neutral land, building, and road colors plus subdued blue-gray water avoid competing with the terrain gradient's blue, teal, yellow, orange, and red scale.
- Basemap geometry is below the terrain image while vector labels remain above it for legibility.
- Optional MapLibre style URL or vector TileJSON override for switching providers without changing source code.

`40 m` is a frequency-response parameter, not a kernel diameter or a claim that 40 m terrain features are noise. A sinusoidal elevation component at that wavelength is attenuated to approximately half amplitude.

## Execution model

At API startup, the backend loads the complete raster, removes its best-fitting plane, computes the DCT once, and retains the spectrum, plane, and discrete biharmonic eigenvalues in memory. For each new setting it applies the spectral response, performs an inverse DCT, differentiates at the 2 m analysis resolution, and then resizes the resulting magnitude for display.

The response is an indexed, georeferenced PNG. MapLibre displays it as one image source over the raster bounds. The backend keeps a small in-memory PNG cache and serializes calculations to limit peak memory use.

Endpoints:

- `GET /api/config` — bounds, image corners, source shape, control limits, and legend.
- `GET /api/terrain.png?wavelength_m=40&max_size=1600` — rendered slope magnitude.

The image response includes `X-Runmap-Compute-Ms` and `X-Runmap-Wavelength-M` headers. A cached response reports zero compute milliseconds.

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

FastAPI serves `web/dist` when it exists. Set `RUNMAP_ELEVATION_PATH` to use another complete single-band raster. Set `VITE_MAP_STYLE_URL` when building or running Vite to replace the default OpenStreetMap vector basemap with a complete MapLibre style:

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

These are development observations, not performance guarantees.

## Current limitations and next slice

- The first view is a single image overlay, not a tile pyramid. Tiling becomes useful when coverage or output size grows.
- Dynamic output currently shows magnitude only. Sparse uphill arrows and click inspection follow next.
- The numerical calculation remains full-source even when only part of the map is visible.
- The DCT reflecting boundary can affect the outer edge; the UI does not yet distinguish a trusted interior display crop.
- The slider uses server-rendered colours. Independent client-side recolouring would require sending numerical data or a custom texture/shader representation.

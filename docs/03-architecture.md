# 3. Architecture and software

## Design principles

- Keep scientific calculations independent of HTTP, file formats, and rendering.
- Retain reusable intermediate results so experiments do not require repeated downloads or raster reads.
- Prefer clear functions and explicit data structures over a general plugin framework.
- Add infrastructure when a concrete requirement or measurement justifies it.

## Initial stack

| Layer | Choice | Responsibility |
| --- | --- | --- |
| Processing | Python | Pipeline orchestration and experiment code |
| Raster access | Rasterio / GDAL | GeoTIFF reads, raster preparation, optional terrain derivatives |
| Numerical operations | NumPy | Array calculations over profiles and rasters |
| Geometry | Shapely | Path geometry and spatial operations |
| Coordinates | pyproj | Coordinate transformations |
| API | FastAPI | Thin access layer for measurements and interactive calculations |
| UI | TypeScript + HTML/CSS | Controls, selection, and API calls |
| Map | MapLibre GL JS | Map rendering and numeric-attribute styling |
| Frontend tooling | Vite | Local development and static production build |

No UI framework or database server is required initially. Python uses compiled geospatial and array libraries internally; custom compiled code is optional.

## Component boundaries

| Component | Input | Output |
| --- | --- | --- |
| Sources | Area and source configuration | Local source files and provenance |
| Paths | OSM extract and access rules | Connected candidate path geometry with tags |
| Elevation | Coordinates and raster source | Sampled elevations with validity information |
| Smoothing | Elevation window, validity mask, metre-based settings | Filtered elevation surface and processing metadata |
| Terrain derivatives | Elevation surface and grid transform | East/north gradient components, magnitude, direction, validity |
| Profiles | Paths and sampling settings | Distance/elevation arrays keyed by path |
| Analysis | Profiles and explicit settings | Gradients, climb summaries, quality flags |
| Publication | Geometry and measurements | Map features/tiles and lookup data |
| API | Validated requests | Profile data or calculation results |
| UI | Map data, API results, user settings | Map, legend, and selected-path details |

The analysis module must not import the API or UI layer. Notebooks and scripts should call the package's analysis functions rather than contain separate production implementations.

## Proposed source layout

This is a target layout, not a requirement to scaffold every directory immediately.

```text
src/runmap/
  sources/       # Source acquisition and provenance
  paths/         # OSM selection and geometry
  elevation/     # Raster access and interpolation
  terrain/       # Replaceable smoothers and raster derivatives
  profiles/      # Sampling and cached profiles
  analysis/      # Mathematical definitions
  publication/   # Display features and tiles
  api/           # FastAPI endpoints
  cli.py         # Repeatable preparation commands
web/src/
  map.ts         # Map sources, layers, selection
  controls.ts    # Settings and legend
  api.ts         # Backend requests
  main.ts        # UI wiring
experiments/     # Small scripts or notebooks using the package
tests/          # Numerical and integration checks
data/           # Local source and generated data; not source-controlled by default
```

## Execution model

1. An explicit preparation command downloads/reads sources and generates terrain derivatives; path profiles follow in a later milestone.
2. Static map assets serve routine navigation where possible.
3. The API reads prepared results or calculates a bounded requested profile.
4. Area-wide rebuilds run as preparation jobs, not long blocking web requests.

Start with a single local machine. A job queue, multiple services, or PostGIS are later options rather than initial dependencies.

For the first experiment, keep `smooth(surface, mask, grid, settings)` and `differentiate(surface, mask, grid)` separate. Start with raw, Gaussian, and regularized nonlinear-diffusion candidates. A compiled extension may later accelerate a measured diffusion bottleneck as well as profile calculations. No compiled implementation is currently required.

## Compiled extension boundary

If wanted, a numerical implementation can accept distance/elevation arrays and settings, and return gradients or climb summaries. Keep GeoTIFF handling and coordinate transformations in the existing Python stack.

Odin is the preferred candidate for an optional numerical learning experiment; Go remains an alternative for a standalone worker or batch command. Choose the integration method only when the experiment is scoped. Preserve the Python implementation as a correctness reference.

## References

- [Rasterio concurrency and native operations](https://rasterio.readthedocs.io/en/stable/topics/concurrency.html)
- [Shapely](https://shapely.readthedocs.io/en/stable/)
- [pyproj](https://pyproj4.github.io/pyproj/stable/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [MapLibre GL JS](https://maplibre.org/maplibre-gl-js/docs/)
- [Vite](https://vite.dev/guide/)

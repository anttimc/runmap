# Runmap

A hobby project for exploring the hilliness of running paths and routes, with understandable, replaceable components.

The first goal is a map that helps identify flatter running options and useful training hills. The project is also a place to experiment with elevation data and mathematics.

## Dependencies

Install the following tools before developing or running the project:

- Python 3.11 or later — backend, terrain processing, optimization, and
  command-line tools.
- Node.js 24.21.0 — frontend build and development server. The `node` command must be available on your shell `PATH`.
- pnpm 12.9.1 — frontend package manager and script runner. The frontend is intentionally managed with pnpm, not npm.

The Python package declares these runtime dependencies: NumPy 2.x, Rasterio
1.x, and SciPy 1.x. The optional `web` extra adds ContourPy 1.x, FastAPI 0.x,
Pillow 11–12, and Uvicorn 0.x; the optional `experiments` extra adds Matplotlib
3.x.
[`requirements-inspection.txt`](requirements-inspection.txt) is the pinned,
tested inspection environment; it includes the direct packages above and their
Python dependencies.

The frontend declares MapLibre GL JS 6.x, with TypeScript 5.x and Vite 7.x as
development dependencies. Exact resolved frontend versions are recorded in
[`web/pnpm-lock.yaml`](web/pnpm-lock.yaml).

For source-elevation downloads, copy [`.env.example`](.env.example) to `.env`
and provide an MML API key. It is not needed to run the application with
already prepared local data.

### Install and run locally

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[web]'

cd web
pnpm install
pnpm dev
```

Run the backend in another terminal from the repository root:

```sh
PYTHONPATH=src .venv/bin/python -m uvicorn runmap.api.app:app --reload
```

## Plan hierarchy

Start with the [documentation index](docs/README.md) and the active
[map-first running plan](docs/22-map-first-running-plan.md).

The sequence is path-gradient exploration and reachability, guided route
assembly, then automatic suggestions. The terrain map, attributed path graph,
and diverse-route baseline are the retained foundation.

The [archive](docs/archive/README.md) contains superseded cycle-first plans and
Git recovery instructions. Their CLI commands and implementation were removed.

## Current status

The named public region is a grid-aligned 40 × 40 km square centered near
Leppävaara. Its 64 analysis cores use 100 shared MML elevation tiles once the
512 m processing halo is included. Source tiles are downloaded, validated,
converted to COGs, cataloged, and activated through an immutable coverage
manifest. The default 40 m spectral thin-plate result is precomputed as derived
COGs and served to MapLibre as versioned Web Mercator XYZ PNG tiles. Other
advertised smoothing presets are generated lazily. The old single-raster image
path remains available as an explicit development override. See the [region and
hosting design](docs/11-region-and-hosting-design.md).

The active study area is **Leppävaara, Espoo, Finland**. Terrain-gradient
magnitude, smoothing comparisons, and the first interactive map are working;
fixed source-elevation contours now show the gradient axis and terrain shape.
The map now supports along-path gradient colors and click inspection with a
relative height profile, direction reversal, uphill sections, and explicit
uncertainty/coverage states. Opt-in outing inclusion now lets you select an
eligible snapped start, choose a nearby alternative, and inspect which complete
path intervals fit a distance budget with retracing allowed. Surface and stair
policies apply consistently to inspection, snapping, and search; gentler-grade
emphasis changes display only. Guided routing and automatic suggestions follow. The routing baseline remains a command-line
experiment.

Path analysis uses optional, explicitly prepared local graph measurements. To
refresh an existing extract without downloading OSM data:

```sh
PYTHONPATH=src .venv/bin/python -m runmap.cli paths prepare leppavaara-flat-10k --input data/paths/raw/leppavaara-flat-10k/osm-network-boundaries-101.json
```

The default API graph is `data/paths/prepared/leppavaara-flat-10k/graph.json`;
set `RUNMAP_PATH_GRAPH` to select another prepared dataset, then restart the
backend. Missing path artifacts leave the terrain map available. Path settings
remain fixed at preparation time; the terrain-background slider changes only
the background. See [M4 delivery details](docs/23-m4-path-exploration.md).

## Keeping the plan useful

- Update goals when the intended outcomes change.
- Update milestone status when its completion criteria are met.
- Record consequential choices in the decision log, including why they were made.
- Treat examples and provisional values as experiments, not settled requirements.
- Keep implementation tasks under the milestone they support; add detailed tasks when that milestone is ready to start.

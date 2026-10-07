# Runmap

A hobby project for exploring the hilliness of running paths and routes, with understandable, replaceable components.

The first goal is a map that helps identify flatter running options and useful training hills. The project is also a place to experiment with elevation data and mathematics.

## Dependencies

Install the following tools before developing or running the project:

- Python 3.9 or later — backend, terrain processing, and command-line tools.
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

1. [Goals](docs/01-goals.md) — why the project exists, intended outcomes, and success criteria.
2. [Milestones](docs/02-milestones.md) — the order of work and what completes each stage.
3. [Architecture](docs/03-architecture.md) — component responsibilities, boundaries, and software choices.
4. [Data and analysis](docs/04-data-and-analysis.md) — data flow, storage, measurement questions, and validation.
5. [Decisions and open questions](docs/05-decisions.md) — agreed direction, provisional choices, and unresolved details.
6. [Leppävaara terrain experiment](docs/06-leppavaara-terrain.md) — gradient definitions, smoothing comparisons, and the first map UI.
7. [Download data](docs/07-download-data.md) — API access, reusable downloader, and local commands.
8. [Smoothing comparison results](docs/08-smoothing-comparison-results.md) — first real and synthetic measurements.
9. [Interactive terrain map](docs/09-interactive-map.md) — local API, MapLibre UI, and development commands.
10. [Thin-plate DCT mathematics](docs/10-thin-plate-dct-mathematics.md) — continuous formulation, discrete operator, and boundary behavior.
11. [Region and hosting design](docs/11-region-and-hosting-design.md) — tiled elevation acquisition, region configuration, caches, and public delivery.
12. [Route-planning design](docs/12-route-planning-design.md) — shared routing architecture, environment-first loops, destination-first return routes, and staged implementation.
13. [Runnable-path graph prototype](docs/13-path-graph-prototype.md) — bounded OSM extraction, directed pedestrian graph, terrain enrichment, and first measured result.
14. [Diverse-path route baseline](docs/14-diverse-route-baseline.md) — shortest-path primitives, outbound/return diversity, anchor loops, route constraints, and measured alternatives.
15. [Cyclic topology and half-edge faces](docs/15-cyclic-topology.md) — bridges, 2-core, cyclic blocks, bounded planar faces, and shared-edge cancellation.
16. [Face-region loop search](docs/16-face-region-loop-search.md) — connected-face growth, single-cycle validation, directed route extraction, and baseline comparison.
17. [Route profiles and superfaces](docs/17-route-profiles-and-superfaces.md) — surface- and stair-aware filtering, topology simplification, and shape-aware beam search with face chunks.
18. [Cycle-catalog route search](docs/18-cycle-catalog-route-search.md) — preprocess reusable good loops, index their joins, and compose them for request-specific length and training goals.

Goals use `G` identifiers, milestones use `M`, and decisions/questions use `D`/`Q`. These identifiers connect implementation work to its purpose.

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
Elevation labels or click inspection can be added later if gradient sign is
needed. Runnable-path and route analysis follow that refinement.

## Keeping the plan useful

- Update goals when the intended outcomes change.
- Update milestone status when its completion criteria are met.
- Record consequential choices in the decision log, including why they were made.
- Treat examples and provisional values as experiments, not settled requirements.
- Keep implementation tasks under the milestone they support; add detailed tasks when that milestone is ready to start.

# Development and data preparation

Run commands from the repository root. Python commands use uv; frontend commands
use pnpm. Python 3.11+ is required by [pyproject.toml](../pyproject.toml).
Node.js must be on PATH; the previously recorded frontend toolchain was Node.js
24.21.0 and pnpm 12.9.1. The frontend declares its package manager in
[package.json](../web/package.json), with resolved dependencies in the pnpm lockfile.

The integration extension requires Rust on PATH before installing from source.
The current local toolchain is Rust 1.99.1, PyO3 0.30.1, and Maturin 1.15.0;
the crate declares Rust 1.85 as its minimum. Only the current local toolchain has
been verified so far. Cargo and uv lockfiles record dependencies.

## Install and run

```sh
uv sync --extra web --extra experiments
pnpm --dir web install
```

The web extra includes the API, image, and geometry dependencies; experiments
adds Matplotlib. The old `requirements-inspection.txt` records a historical
inspection environment and is not the application setup procedure.

## Native integration kernel

Install the compiler and Cargo with [Rustup](https://rust-lang.org/tools/install/)
before the initial `uv sync`. This session installed Rustup's minimal stable
toolchain without modifying shell startup files. In a new shell, expose it with:

```sh
source "$HOME/.cargo/env"
rustc --version
cargo --version
```

`uv sync --extra web --extra experiments` builds the native extension as part of
installing the project. The default development dependency group supplies
Maturin. After editing Rust source, explicitly rebuild the optimized extension:

```sh
uv run --no-sync maturin develop --release --locked --uv
```

Maturin compiles and installs the mixed Python/Rust package; PyO3 supplies the
bindings between Python objects and Rust values. `pyproject.toml` points to
`rust/Cargo.toml` and names the extension `runmap._routing_native`. Python sources
remain under `src/runmap`. Cargo's `python` feature enables only the binding layer,
so pure Rust tests do not need a Python interpreter. The `abi3-py311` setting
targets CPython's stable extension ABI starting at Python 3.11; runtime validation
across supported Python versions remains outstanding. See the
[Maturin layout documentation](https://www.maturin.rs/project_layout.html) and
[PyO3 guide](https://pyo3.rs/v0.30.1/).

Start reading at [the scalar kernel](../rust/src/fields.rs), then
[the binding](../rust/src/bindings.rs), and finally
[the Python wrapper](../src/runmap/routing/fields.py). The kernel takes a borrowed
slice of coordinate pairs and returns three `f64` values. Settings are plain
data; numerical operations are functions. Empty/single-point geometries return
zero integrals; invalid coordinates/scales raise `ValueError`, and deadline
expiry raises `TimeoutError`. The Python reference is available explicitly as
`integrate_fields_python`. A missing compiled extension is an installation error,
not a reason to silently benchmark Python instead.

```sh
uv run --no-sync python -c 'from runmap.routing.fields import integrate_fields; print(integrate_fields([(0., 0.), (100., 0.)], (0., 0.), (100., 0.), 15., 5.))'
```

Compiled extensions and `rust/target/` are ignored by Git. Restart the backend
after rebuilding; an already running process retains its loaded native library.

With terrain prepared as below, start the backend:

```sh
uv run --extra web python -m uvicorn runmap.api.app:app --reload
```

In a second terminal:

```sh
pnpm --dir web dev
```

Vite proxies `/api` to port 8000. For a production-style local server, build with
`pnpm --dir web build` and run Uvicorn without `--reload`; FastAPI serves
`web/dist` when present. Use one API worker because exploration snapshots and
their identifiers are process-local.

## Prepare elevation

Source data and generated artifacts are ignored by Git. Copy
[.env.example](../.env.example) to `.env` and supply an MML API key for acquisition.
The `MML_API_KEY` environment variable takes precedence. Prepared local data
does not require a key during application use.

```sh
uv run --extra web python -m runmap.cli region list
uv run --extra web python -m runmap.cli region prepare leppavaara --dry-run
uv run --extra web python -m runmap.cli region prepare leppavaara
uv run --extra web python -m runmap.cli region precompute leppavaara --wavelength-m 40
```

Preparation downloads missing source tiles, validates their structure and finite
coverage, converts them to COGs, and activates an immutable coverage manifest
under `data/elevation/`. Ready source tiles are reused. Precompute builds the
default derived terrain; other advertised presets are generated on demand.
The dry run lists required tiles without downloading them. See
[architecture](architecture.md) for grids and artifact identities.

## Prepare paths

Prepare elevation and its default derivative first, then acquire and prepare the
configured OSM network:

```sh
uv run --extra web python -m runmap.cli paths prepare helsinki-capital-area --dry-run
uv run --extra web python -m runmap.cli paths prepare helsinki-capital-area
```

Rebuild from an existing local extract without downloading again:

```sh
uv run --extra web python -m runmap.cli paths prepare helsinki-capital-area \
  --input data/paths/raw/helsinki-capital-area/osm-network-boundaries-none.json
```

Use the actual cached extract filename if different. Outputs under
`data/paths/prepared/helsinki-capital-area/` include `graph.json`, content-addressed
measurements, `segments.geojson`, and `inspection.json`. `--without-terrain`
supports topology inspection but does not supply the measurements required by
the web path service. Restart the backend after replacing prepared artifacts.

The default capital-area network uses the existing elevation region's full
40 × 40 km EPSG:3067 footprint `[360000, 6660000, 400000, 6700000]`, rather than
municipal boundaries. It reuses the prepared 40 m terrain product without new
elevation downloads. OSM acquisition includes complete ways intersecting its
WGS84 bounding box; portions outside terrain coverage retain missing-terrain
flags. The original `leppavaara-flat-10k` experiment remains available for small
benchmarks and the anchor baseline. To run the app with that smaller graph, set
`RUNMAP_PATH_GRAPH=data/paths/prepared/leppavaara-flat-10k/graph.json`.

## Configuration

Region definitions live in `config/regions/`; path/baseline settings live in
`config/routing-experiments/`. Private region definitions can live in the ignored
`config/regions.local/` directory. The current server has no private-region
authorization layer; do not expose a deployment containing private coverage.

| Variable | Default / purpose |
| --- | --- |
| `MML_API_KEY` | Acquisition credential; also read from `.env` by the CLI |
| `RUNMAP_REGION_ID` | `leppavaara` |
| `RUNMAP_REGION_DIRECTORY` | Project `config/regions` |
| `RUNMAP_DATA_ROOT` | Project `data/elevation` for the API |
| `RUNMAP_CATALOG_PATH` | `catalog.sqlite` under the elevation root |
| `RUNMAP_ELEVATION_PATH` | Optional complete single-band raster; overrides named-region terrain |
| `RUNMAP_PATH_GRAPH` | Project `data/paths/prepared/helsinki-capital-area/graph.json` |
| `RUNMAP_RUN_DEADLINE` | 15 seconds per planner request |
| `RUNMAP_PATH_PREPARATION_DEADLINE` | 10 seconds per exploration snapshot preparation |
| `RUNMAP_PATH_TILE_CACHE_BYTES` | 67,108,864 bytes |
| `RUNMAP_PATH_SNAPSHOT_CACHE_BYTES` | 134,217,728 bytes |
| `RUNMAP_PATH_TILE_TTL` | 900 seconds |
| `RUNMAP_PATH_SNAPSHOT_TTL` | 900 seconds |
| `VITE_MAP_STYLE_URL` | Optional replacement MapLibre style, selected at development/build time |
| `VITE_OSM_VECTOR_TILEJSON_URL` | Optional replacement Shortbread-compatible vector endpoint |

CLI root/config overrides are options before their subcommand; inspect, for
example, `uv run --extra web python -m runmap.cli region --help`. There is no
implemented `RUNMAP_PUBLIC_ACQUISITION` switch or automatic source-refresh job.

## Verification

For the first Rust checkout, install the formatting/linting components with
`rustup component add rustfmt clippy`. Then run:

```sh
cargo fmt --manifest-path rust/Cargo.toml -- --check
cargo test --manifest-path rust/Cargo.toml --locked
cargo clippy --manifest-path rust/Cargo.toml --locked --all-targets -- -D warnings
uv run --no-sync cargo clippy --manifest-path rust/Cargo.toml --locked --all-targets --features python -- -D warnings
uv run --extra web --extra experiments python -m unittest discover -s tests
pnpm --dir web build
pnpm --dir web test
pnpm --dir web format:check
```

The Python field tests compare the native implementation with scalar midpoint
calculations and the prior Python integrator, including narrow/wide fields and
deadline expiry during a long native call. Rebuild Rust before running Python
checks after native edits; uv does not detect every Rust source change itself.

The Python suite covers terrain, graph/policy behavior, measurements, exploration,
tiles/caches, routing costs, and API contracts. The frontend build includes
TypeScript checking, including unused declarations and parameters. The frontend
tests use Node's built-in test runner with TypeScript stripping; use the
recorded Node 24 toolchain. Use `pnpm --dir web format` to apply
Prettier to frontend TypeScript, HTML, CSS, scripts, tests, and configuration.
Browser/local-runner acceptance remains separate in the
[roadmap](roadmap.md). For an already synchronized environment, use
`uv run --offline --no-sync python …` to avoid dependency resolution.

## Experiments and reports

The CLI anchor baseline remains a separate experiment, with its own distance,
ascent, stairs, and overlap settings:

```sh
uv run --extra web python -m runmap.cli routes baseline leppavaara-flat-10k
uv run --extra experiments python -m runmap.experiments.smooth_loops
uv run --extra experiments python -m runmap.experiments.smooth_loop_obstacles
uv run --extra experiments python -m runmap.experiments.plot_hill_penalties
```

Compare the release-built Rust integrator with the Python reference on the real
profile route, including conversion overhead and fresh per-request field caches:

```sh
uv run --extra web python -m runmap.experiments.benchmark_integration \
  --repeats 20 --output artifacts/pathfinding-profile/first-rust/integration-benchmark.json
```

This reports median/p95 integration and planner times and checks both legs' paths
and costs. It excludes dataset loading, snapping, HTTP delivery, and rendering.
The [first Rust report](archive/history/33-rust-integration-kernel.md) records the
initial result; wider interaction and concurrency checks remain in the roadmap.

Benchmark the Python A* integer index against the retained string-indexed baseline:

```sh
uv run --extra web python -m runmap.experiments.benchmark_integer_ids \
  --repeats 20 --output artifacts/pathfinding-profile/integer-ids/benchmark.json
```

Both searches remain Python. Complete-route comparisons use the same integrator
on both sides, with an additional profile using Python integration explicitly.
The benchmark alternates implementations, verifies paths/costs/expansion counts,
includes endpoint-overlay preparation, and reports isolated length-only search,
index preparation time, and retained allocations. See the
[integer-index report](archive/history/34-integer-search-index.md) for scope and
the initial measured tradeoffs.

Baseline results go under `data/routes/prepared/`; synthetic results and hill
plots go under `data/experiments/`. For scripts taking explicit report paths,
create a fresh output directory and retain the model/dataset identity:

```sh
mkdir -p data/experiments/review
uv run --extra web python -m runmap.experiments.audit_routing_preferences \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  data/experiments/review/preferences-audit.json
uv run --extra web python -m runmap.experiments.benchmark_hill_preferences \
  data/paths/prepared/leppavaara-flat-10k/graph.json \
  data/experiments/review/hills.json
```

`benchmark_path_tiles` and `benchmark_building_access` similarly take graph and
output paths. The browser tile benchmark requires API/Vite and Chrome remote
debugging on port 9225:

```sh
node web/scripts/benchmark-path-tiles.mjs data/experiments/review/path-tiles-browser.json
```

Publish selected dated evidence in the [archive](archive/README.md), keeping
reports and supporting images together. Do not overwrite historical evidence
to imply that it validates the current model.

## Sources and attribution

Acquisition preserves provider, retrieval time, bounds, checksums, and source
provenance. Keep MML Elevation model 2 m attribution and its recorded CC BY 4.0
license metadata with published derivatives; OSM has separate contributor/ODbL
attribution. The default basemap uses OSM Shortbread vector tiles. Provider
terms and production hosting suitability must be checked before publication;
no public deployment is part of the current setup.

Source references: [MML dataset](https://www.maanmittauslaitos.fi/en/maps-and-spatial-data/datasets-and-interfaces/product-descriptions/elevation-model-2-m),
[OSM attribution](https://www.openstreetmap.org/copyright), and
[OSM vector tile policy](https://operations.osmfoundation.org/policies/vector/).

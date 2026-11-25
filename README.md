# Runmap

**Simple running route optimization based on your preferences**

Getting from A to B is only part of planning a run. You might want a loop from
home, gentler climbs, more greenery, a stretch by the water, or fewer stops at
traffic lights. Runmap brings those choices into route planning, with a map
that helps you see the terrain before you head out.

## Build the run around you

- **Shape a round trip.** Pick a start and turnaround, then adjust loop width
  and direction to explore different routes home. One-way planning is available
  too.
- **Start with a distance in mind.** Enter a target loop length to highlight
  estimated turnaround locations, choose one on the map, and generate a route.
  The estimate guides your choice; the resulting distance can differ.
- **Choose the surroundings.** Prefer greenery or proximity to water, reduce
  modeled traffic exposure, and avoid mapped wetlands. Set hill and
  traffic-light avoidance independently, with surface and stair controls for
  the paths you want to run.
- **Read the landscape.** Explore terrain slope, path gradients, flat stretches,
  and distance from your start—even before generating a route. Inspect the
  chosen run's elevation profile and estimated ascent and descent.
- **Keep your preferences and take the route with you.** Save named presets in
  your browser, download a GPX track, or use the optional local Suunto integration
  to send a route for watch sync.

For example, plan a loop from home with a roughly 10 km target, prefer greenery,
turn up hill avoidance for an easier day, and check the elevation profile before
exporting it. Preferences guide route choices; available paths and prepared data
determine what is possible.

## Current availability

Runmap is a hobby project that runs locally with prepared data. Current coverage
is a 40 × 40 km area around Leppävaara in the Helsinki capital region. Data is
not bundled with the repository, so a fresh checkout needs data preparation
before route planning is available. Environment and traffic-light preferences
require their corresponding prepared datasets.

Terrain and surroundings are estimates based on mapped data, rather than live
path conditions or measured traffic noise. See the [guide](docs/guide.md) for
current behavior and limitations. Cloud hosting and wider coverage in Finland
are future ambitions; see the [goals](docs/goals.md) for direction.

## Quick start

Install Python 3.11+, uv, the Rust toolchain, Node.js, and pnpm. Rust builds the
integration extension; see [native setup](docs/development.md#native-integration-kernel).
From the repository root, with local terrain data already prepared:

```sh
uv sync --extra web
uv run --extra web python -m uvicorn runmap.api.app:app --reload
```

In another terminal:

```sh
pnpm --dir web install
pnpm --dir web dev
```

Open the URL printed by Vite. Data is not included in Git; follow
[development and data preparation](docs/development.md) for a fresh checkout.
Missing path artifacts leave terrain exploration available.
The default path backend requires an exported sibling `network.sqlite`; see
[SQLite setup](docs/development.md#export-a-sqlite-network-dataset). Exploration
and run planning load bounded local graphs without the source JSON. A matching
`numeric-routing-v2.sqlite` (or a converted version-3 generation) enables native
run planning and distance exploration
with a bounded working-set cache; see the SQLite setup instructions above.

## Documentation

- [Guide](docs/guide.md): existing features and limitations.
- [Goals](docs/goals.md): purpose, scope, and non-goals.
- [Roadmap](docs/roadmap.md): outstanding work and acceptance criteria.
- [Architecture](docs/architecture.md): components, dependencies, and data flow.
- [Development](docs/development.md): setup, commands, and configuration.
- [Terrain mathematics](docs/reference/terrain-mathematics.md) and
  [routing mathematics](docs/reference/routing-mathematics.md): implemented calculations.
- [Documentation maintenance](docs/README.md) and [historical archive](docs/archive/README.md).

## License

[MIT](LICENSE).

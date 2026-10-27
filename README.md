# Runmap

A hobby project for exploring running-path hilliness across the Helsinki capital area,
and experimenting with elevation data and routing mathematics.

The map supports terrain and path-gradient exploration, path inspection,
distance shells from a selected start, and home–turnaround–home run
planning with optional hill avoidance. See the [guide](docs/guide.md) for
behavior and limitations, and the [goals](docs/goals.md) for intended outcomes.

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
`numeric-routing-v2.sqlite` enables native run planning and distance exploration
with a bounded working-set cache; see the SQLite setup instructions above.

## Documentation

- [Guide](docs/guide.md): existing features and limitations.
- [Goals](docs/goals.md): purpose, scope, and non-goals.
- [Roadmap](docs/roadmap.md): outstanding work and acceptance criteria.
- [Architecture](docs/architecture.md): current data flow and implementation.
- [Development](docs/development.md): setup, commands, and configuration.
- [Terrain mathematics](docs/reference/terrain-mathematics.md) and
  [routing mathematics](docs/reference/routing-mathematics.md): implemented calculations.
- [Documentation maintenance](docs/README.md) and [historical archive](docs/archive/README.md).

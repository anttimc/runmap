# Runmap

A hobby project for exploring the hilliness of running paths and routes, with understandable, replaceable components.

The first goal is a map that helps identify flatter running options and useful training hills. The project is also a place to experiment with elevation data and mathematics.

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

Goals use `G` identifiers, milestones use `M`, and decisions/questions use `D`/`Q`. These identifiers connect implementation work to its purpose.

## Current status

The Leppävaara 6 × 6 km elevation crop has been downloaded and structurally validated. Reusable acquisition, smoothing, differentiation, and comparison components are available. Spectral thin-plate smoothing with a 40 m half-amplitude wavelength is the current default. A local interactive MapLibre prototype now displays dynamically rendered slope magnitude and allows the smoothing scale and overlay opacity to change. The next scaling step is a shared 5 km elevation-tile catalog with lightweight named regions; see the [region and hosting design](docs/11-region-and-hosting-design.md).

The study area is **Leppävaara, Espoo, Finland**. The first visualization will show terrain-gradient magnitude and direction, with a comparison of smoothing methods. M0 is active: exact bounds, validation locations, and experimental parameters remain to be selected. Path and route analysis follow the terrain experiment.

## Keeping the plan useful

- Update goals when the intended outcomes change.
- Update milestone status when its completion criteria are met.
- Record consequential choices in the decision log, including why they were made.
- Treat examples and provisional values as experiments, not settled requirements.
- Keep implementation tasks under the milestone they support; add detailed tasks when that milestone is ready to start.
